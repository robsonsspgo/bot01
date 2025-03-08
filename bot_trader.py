import tkinter as tk
from tkinter import ttk
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np
import threading
import time
import os
from datetime import datetime
from binance.client import Client
from binance.exceptions import BinanceAPIException, BinanceOrderException

# Configurar estilo visual
style = ttk.Style()
style.theme_use('clam')
style.configure('.', background='#2d2d2d', foreground='white')
style.configure('TLabel', font=('Arial', 10))
style.configure('TButton', font=('Arial', 10, 'bold'), padding=6)
style.configure('TLabelframe', background='#2d2d2d', bordercolor='#404040')
style.configure('TLabelframe.Label', background='#2d2d2d', foreground='white')

class TradingBot:
    def __init__(self, api_key, api_secret, testnet=False):
        self.client = Client(api_key, api_secret, testnet=testnet)
        self.symbol = 'SOLBRL'
        self.running = False
        
        # Configurações de estratégia
        self.timeframe = '5m'
        self.history_length = 12
        self.risk_percent = 0.5
        self.stop_loss = 0.02
        self.trailing_stop = 0.015
        self.min_volume_factor = 1.5
        self.daily_loss_limit = -0.05
        self.max_trades_day = 5
        
        # Estado da operação
        self.shares = 0
        self.entry_price = 0.0
        self.highest_price = 0.0
        self.today_trades = 0
        self.daily_balance = 0.0
        self.last_trade_date = datetime.now().date()
        self.history = []
        
        # Inicialização segura
        self.data_file = self.get_data_path()
        self.load_history()
        self.reset_daily_stats()

    def get_data_path(self):
        script_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(script_dir, "dados.csv")

    def load_history(self):
        try:
            with open(self.data_file, "r") as file:
                self.history = [float(line.strip()) for line in file]
        except (FileNotFoundError, ValueError):
            print("Iniciando novo histórico de dados")
            self.history = []

    def save_history(self):
        with open(self.data_file, "w") as file:
            for price in self.history:
                file.write(f"{price}\n")

    def reset_daily_stats(self):
        self.today_trades = 0
        self.daily_balance = self.get_current_balance()
        self.last_trade_date = datetime.now().date()

    def get_current_balance(self):
        try:
            balance = self.client.get_asset_balance(asset='BRL')
            return float(balance['free'])
        except BinanceAPIException as e:
            print(f"Erro ao obter saldo: {e}")
            return 0.0

    def get_latest_price(self):
        """Obtém o preço atual de forma segura"""
        try:
            ticker = self.client.get_symbol_ticker(symbol=self.symbol)
            return float(ticker['price'])
        except (BinanceAPIException, KeyError) as e:
            print(f"Erro ao obter preço: {e}")
            return 0.0

    def fetch_market_data(self):
        try:
            candles = self.client.get_klines(
                symbol=self.symbol,
                interval=self.timeframe,
                limit=self.history_length
            )
            
            self.history = [{
                'time': candle[0],
                'price': float(candle[4]),
                'volume': float(candle[5])
            } for candle in candles]
            return True
        except Exception as e:
            print(f"Erro ao obter dados: {e}")
            return False

    def calculate_indicators(self):
        try:
            prices = [x['price'] for x in self.history]
            volumes = [x['volume'] for x in self.history]
            
            ma_short = np.mean(prices[-6:]) if len(prices) >= 6 else 0.0
            ma_long = np.mean(prices) if prices else 0.0
            avg_volume = np.mean(volumes[:-1]) if len(volumes) > 1 else 0.0
            
            return ma_short, ma_long, avg_volume
        except Exception as e:
            print(f"Erro ao calcular indicadores: {e}")
            return 0.0, 0.0, 0.0

    def start(self):
        self.running = True
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self.running = False

    def run(self):
        while self.running:
            try:
                self.check_daily_limits()
                
                if not self.fetch_market_data():
                    time.sleep(10)
                    continue
                
                ma_short, ma_long, avg_volume = self.calculate_indicators()
                current_price = self.get_latest_price()
                current_volume = self.history[-1]['volume'] if self.history else 0.0
                
                volume_ok = current_volume > (avg_volume * self.min_volume_factor)
                trend_strength = ma_short - ma_long
                
                if volume_ok:
                    if trend_strength > 0 and not self.shares:
                        self.execute_buy(current_price)
                    elif self.shares > 0:
                        self.check_sell_conditions(current_price)
                
                self.update_trailing_stop(current_price)
                time.sleep(60)
                
            except Exception as e:
                print(f"Erro crítico: {e}")
                time.sleep(30)

    def execute_buy(self, price):
        if self.today_trades >= self.max_trades_day or price <= 0:
            return
            
        try:
            # Obter informações do par
            symbol_info = self.client.get_symbol_info(self.symbol)
            lot_size = next(
                (f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'),
                None
            )
            
            if not lot_size:
                raise ValueError("Filtro LOT_SIZE não encontrado")
                
            # Calcular quantidade com precisão correta
            balance = self.get_current_balance()
            amount = balance * self.risk_percent
            qty = amount / price
            
            # Aplicar regras LOT_SIZE
            step_size = float(lot_size['stepSize'])
            min_qty = float(lot_size['minQty'])
            max_qty = float(lot_size['maxQty'])
            
            # Arredondar para o step size correto
            qty = (qty // step_size) * step_size
            qty = round(qty, 8)  # Binance requer 8 casas decimais
            
            if amount >= 10 and min_qty <= qty <= max_qty:
                order = self.client.create_order(
                    symbol=self.symbol,
                    side=Client.SIDE_BUY,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=qty
                )
                self.shares = qty
                self.entry_price = price
                self.highest_price = price
                self.today_trades += 1
                print(f"COMPRA: {qty} SOL a R${price:.2f}")
            else:
                print(f"Quantidade inválida: {qty} (Min: {min_qty}, Max: {max_qty})")
                
        except Exception as e:
            print(f"Falha na compra: {e}")

    def check_sell_conditions(self, current_price):
        if current_price <= self.entry_price * (1 - self.stop_loss):
            self.execute_sell(current_price, 'STOP LOSS')
            return
            
        trail_price = self.highest_price * (1 - self.trailing_stop)
        if current_price <= trail_price:
            self.execute_sell(current_price, 'TRAILING STOP')
            return
            
        if current_price >= self.entry_price * 1.03:
            self.execute_sell(current_price, 'TAKE PROFIT PARCIAL')

    def execute_sell(self, price, reason):
        try:
            if self.shares <= 0 or price <= 0:
                return
                
            # Obter informações do par
            symbol_info = self.client.get_symbol_info(self.symbol)
            lot_size = next(
                (f for f in symbol_info['filters'] if f['filterType'] == 'LOT_SIZE'),
                None
            )
            
            if not lot_size:
                raise ValueError("Filtro LOT_SIZE não encontrado")
                
            # Aplicar regras LOT_SIZE
            step_size = float(lot_size['stepSize'])
            min_qty = float(lot_size['minQty'])
            
            qty = self.shares
            qty = (qty // step_size) * step_size
            qty = round(qty, 8)
            
            if qty >= min_qty:
                order = self.client.create_order(
                    symbol=self.symbol,
                    side=Client.SIDE_SELL,
                    type=Client.ORDER_TYPE_MARKET,
                    quantity=qty
                )
                self.shares = 0
                print(f"VENDA ({reason}): {qty} SOL a R${price:.2f}")
                self.update_balance()
            else:
                print(f"Quantidade inválida para venda: {qty}")
            
        except Exception as e:
            print(f"Falha na venda: {e}")

    def update_trailing_stop(self, current_price):
        if current_price > self.highest_price:
            self.highest_price = current_price

    def check_daily_limits(self):
        today = datetime.now().date()
        if today != self.last_trade_date:
            self.reset_daily_stats()
            
        current_balance = self.get_current_balance()
        if self.daily_balance == 0:
            self.daily_balance = current_balance  # Evita divisão por zero
            
        daily_pnl = (current_balance - self.daily_balance) / self.daily_balance if self.daily_balance != 0 else 0
        
        if daily_pnl <= self.daily_loss_limit:
            print(f"STOP DIÁRIO ATINGIDO: {daily_pnl*100:.2f}%")
            self.stop()

class TradingApp(tk.Tk):
    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.title("Smart Trading Bot")
        self.geometry("1000x700")
        self.configure(bg='#2d2d2d')
        self.after_id = None
        
        self.setup_ui()
        self.update_gui()
        self.protocol("WM_DELETE_WINDOW", self.on_closing)

    def setup_ui(self):
        main_frame = ttk.Frame(self)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Gráfico
        chart_frame = ttk.LabelFrame(main_frame, text=" Análise de Mercado ")
        chart_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        self.fig, self.ax = plt.subplots(figsize=(10, 4), facecolor='#1e1e1e')
        self.ax.tick_params(colors='white')
        self.fig.patch.set_facecolor('#1e1e1e')
        self.canvas = FigureCanvasTkAgg(self.fig, chart_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # Controles
        control_frame = ttk.Frame(main_frame)
        control_frame.pack(fill=tk.X, pady=5)
        
        self.start_btn = ttk.Button(control_frame, text="▶ INICIAR", command=self.bot.start)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        
        self.stop_btn = ttk.Button(control_frame, text="⏹ PARAR", command=self.bot.stop)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        # Painel de status
        status_frame = ttk.LabelFrame(main_frame, text=" Status da Operação ")
        status_frame.pack(fill=tk.BOTH, pady=5)

        # Colunas de informações
        cols = ttk.Frame(status_frame)
        cols.pack(fill=tk.BOTH, expand=True)
        
        self.create_status_column(cols, "Finanças", [
            ("Preço Atual:", "price", "#00ff00"),
            ("Saldo BRL:", "balance", "white"),
            ("Posição:", "position", "#00ffff")
        ])
        
        self.create_status_column(cols, "Desempenho", [
            ("Lucro/Prejuízo:", "profit", "auto"),
            ("Volume 24h:", "volume", "white"),
            ("Trades Hoje:", "trades", "white")
        ])
        
        self.create_status_column(cols, "Risco", [
            ("Nível Risco:", "risk", "auto"),
            ("Stop Atual:", "stop", "#ff5555"),
            ("Sinal:", "signal", "auto")
        ])

    def create_status_column(self, parent, title, items):
        col = ttk.LabelFrame(parent, text=f" {title} ")
        col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        for label_text, key, color in items:
            frame = ttk.Frame(col)
            frame.pack(fill=tk.X, pady=2)
            
            ttk.Label(frame, text=label_text, width=15, anchor=tk.W).pack(side=tk.LEFT)
            lbl = ttk.Label(frame, text="N/A", width=15, anchor=tk.W)
            lbl.pack(side=tk.LEFT)
            setattr(self, f"{key}_label", lbl)
            
            # Configuração inicial de cores
            if color == "auto":
                if key == 'profit':
                    lbl.configure(foreground='#00ff00')
                elif key == 'risk':
                    lbl.configure(foreground='#ffff00')
                elif key == 'signal':
                    lbl.configure(foreground='#ffffff')
            else:
                lbl.configure(foreground=color)

    def update_gui(self):
        try:
            price = self.bot.get_latest_price()
            balance = self.bot.get_current_balance()
            position = self.bot.shares
            volume = self.bot.history[-1]['volume'] if self.bot.history else 0.0
            
            # Cálculo seguro do PnL
            pnl = 0.0
            if self.bot.entry_price > 0 and price > 0:
                pnl = ((price - self.bot.entry_price) / self.bot.entry_price) * 100
                
            risk_level = "Alto" if self.bot.today_trades >= 3 else "Médio" if self.bot.today_trades >= 1 else "Baixo"
            stop_price = self.bot.entry_price * (1 - self.bot.stop_loss) if self.bot.entry_price > 0 else 0.0
            
            # Atualizar labels
            self.price_label.config(text=f"R$ {price:.2f}")
            self.balance_label.config(text=f"R$ {balance:.2f}")
            self.position_label.config(text=f"{position:.4f} SOL")
            self.profit_label.config(text=f"{pnl:+.2f}%")
            self.volume_label.config(text=f"{volume:.0f} SOL")
            self.trades_label.config(text=str(self.bot.today_trades))
            self.risk_label.config(text=risk_level)
            self.stop_label.config(text=f"R$ {stop_price:.2f}" if stop_price else "N/A")
            self.signal_label.config(text=self.get_current_signal())
            
            # Atualizar cores dinamicamente
            self.profit_label.config(foreground='#00ff00' if pnl >= 0 else '#ff0000')
            self.risk_label.config(foreground=self.get_risk_color(risk_level))
            self.signal_label.config(foreground=self.get_signal_color())
            
            # Atualizar gráfico
            self.update_chart()
            
        except Exception as e:
            print(f"Erro na atualização: {e}")
        
        self.after_id = self.after(3000, self.update_gui)

    def update_chart(self):
        self.ax.clear()
        if len(self.bot.history) > 0:
            prices = [x['price'] for x in self.bot.history]
            self.ax.plot(prices, color='#00ff00', linewidth=1)
            self.ax.set_facecolor('#1e1e1e')
            self.canvas.draw()

    def get_risk_color(self, risk_level):
        colors = {
            'Baixo': '#00ff00',
            'Médio': '#ffff00',
            'Alto': '#ff0000'
        }
        return colors.get(risk_level, 'white')

    def get_current_signal(self):
        if self.bot.shares > 0:
            return "POSIÇÃO ABERTA"
        if len(self.bot.history) < 2:
            return "AGUARDANDO"
        return "COMPRAR" if self.bot.history[-1]['price'] > self.bot.history[-2]['price'] else "VENDER"

    def get_signal_color(self):
        signal = self.get_current_signal()
        return '#00ff00' if signal == "COMPRAR" else '#ff0000' if signal == "VENDER" else '#ffffff'

    def on_closing(self):
        if self.after_id:
            self.after_cancel(self.after_id)
        self.bot.save_history()
        self.bot.stop()
        self.destroy()



if __name__ == "__main__":
    # Substituir com chaves válidas
    api_key = 'L7jfCU9nW6YzCqUilv6DgdWVr3zXBJDIGINPnpXH8KRDpden0SAtFTtmwrq4NyUn'
    api_secret = 'htyTH7dEEzlY733E8sNPHeUNXbJZGCoYEvMVeMsrFo5QKMrZSo4G7urfCdPWOpDx'

    # Para usar a testnet da Binance (recomendado para testes)
       
    bot = TradingBot(api_key, api_secret)
    app = TradingApp(bot)
    
    app.mainloop()