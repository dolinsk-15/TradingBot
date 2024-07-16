import datetime
import pytz
import schedule
import pandas_market_calendars as mcal
from ib_insync import *
import pandas as pd
import time
import requests

# Установка соединения с IB Gateway
ib = IB()
ib.connect('127.0.0.1', 4002, clientId=1)

# Настройки стратегии
MAX_POSITION_SIZE = 1000  # Максимальный размер позиции в долларах
TAKE_PROFIT_PERCENT = 0.06 / 100  # Уровень тейк-профита в процентах
STOP_LOSS_PERCENT = 0.08 / 100  # Уровень стоп-лосса в процентах
PARTIAL_TAKE_PROFIT_PERCENT = 0.03 / 100  # Уровень частичного тейк-профита в процентах
TRAILING_STOP_PERCENT = 0.03 / 100  # Уровень трейлинг-стопа в процентах

# Время работы стратегии (Нью-Йоркское время)
ny_tz = pytz.timezone('America/New_York')
START_TIME = datetime.time(10, 0)  # Начало работы (через 30 минут после открытия биржи)
END_TIME = datetime.time(15, 50)  # Конец работы (за 10 минут до закрытия биржи)

# Переменные для отслеживания результатов
trades_count = 0
profitable_trades = 0
loss_trades = 0
capital_growth = 0

# Функция для расчета RSI (Relative Strength Index)
def get_rsi(data, window=14):
    delta = data['close'].diff()  # Изменение цены закрытия
    gain = (delta.where(delta > 0, 0)).fillna(0)  # Выигрыши (только положительные изменения)
    loss = (-delta.where(delta < 0, 0)).fillna(0)  # Потери (только отрицательные изменения)

    avg_gain = gain.rolling(window=window, min_periods=1).mean()  # Средние выигрыши за период
    avg_loss = loss.rolling(window=window, min_periods=1).mean()  # Средние потери за период

    rs = avg_gain / avg_loss  # Отношение выигрышей к потерям
    rsi = 100 - (100 / (1 + rs))  # Рассчет RSI
    return rsi  # Возвращение значения RSI

# Функция для открытия позиции
def open_position(symbol):
    global trades_count, profitable_trades, loss_trades, capital_growth

    contract = Stock(symbol, 'SMART', 'USD')  # Создание контракта на акцию
    ib.qualifyContracts(contract)  # Проверка контракта

    # Получение исторических данных
    bars = ib.reqHistoricalData(
        contract, endDateTime='', durationStr='2 D',  # Запрос данных за последние 2 дня
        barSizeSetting='1 min', whatToShow='MIDPOINT', useRTH=True)  # Использование минутных свечей
    df = util.df(bars)  # Преобразование данных в DataFrame
    df['rsi'] = get_rsi(df)  # Добавление столбца с RSI

    if len(df) < 2:  # Проверка наличия достаточного количества данных
        print("Not enough data to make a decision.")  # Сообщение о недостатке данных
        return  # Выход из функции
   
    previous_candle = df.iloc[-2]  # Получение предыдущей свечи
    current_price = df.iloc[-1]['close']  # Текущая цена закрытия
    rsi = previous_candle['rsi']  # Значение RSI для предыдущей свечи

    # Условия для открытия позиции
    if previous_candle['close'] > previous_candle['open'] and rsi > 50:  # Если предыдущая свеча восходящая и RSI больше 50
        entry_price = previous_candle['open'] + (previous_candle['close'] - previous_candle['open']) / 2  # Вход по середине восхода
    elif previous_candle['close'] < previous_candle['open'] and rsi < 50:  # Если предыдущая свеча нисходящая и RSI меньше 50
        entry_price = previous_candle['close']  # Вход по цене закрытия
    else:
        print("No suitable conditions met.")  # Сообщение о неподходящих условиях
        return  # Выход из функции

    # Расчет количества акций для покупки
    quantity = int(MAX_POSITION_SIZE / current_price)  # Расчет максимального количества акций

    if quantity * current_price > MAX_POSITION_SIZE:  # Проверка превышения максимальной суммы
        quantity -= 1  # Уменьшение количества акций

    if quantity <= 0:  # Проверка на наличие акций для покупки
        print("Not enough funds to open a position.")  # Сообщение о недостатке средств
        return  # Выход из функции

    # Открытие позиции
    order = MarketOrder('BUY', quantity)  # Создание рыночного ордера на покупку
    trade = ib.placeOrder(contract, order)  # Размещение ордера
    ib.sleep(1)  # Ожидание завершения ордера

    # Установка уровней тейк-профита и стоп-лосса
    take_profit_price = entry_price * (1 + TAKE_PROFIT_PERCENT)  # Расчет уровня тейк-профита
    stop_loss_price = entry_price * (1 - STOP_LOSS_PERCENT)  # Расчет уровня стоп-лосса
    partial_take_profit_price = entry_price * (1 + PARTIAL_TAKE_PROFIT_PERCENT)  # Расчет уровня частичного тейк-профита

    def on_new_bar():
        global trades_count, profitable_trades, loss_trades, capital_growth

        bars = ib.reqHistoricalData(
            contract, endDateTime='', durationStr='2 D',  # Запрос данных за последние 2 дня
            barSizeSetting='1 min', whatToShow='MIDPOINT', useRTH=True)  # Использование минутных свечей
        df = util.df(bars)  # Преобразование данных в DataFrame
        current_price = df.iloc[-1]['close']  # Текущая цена закрытия

        # Проверка на достижение уровня тейк-профита
        if current_price >= take_profit_price:
            order = MarketOrder('SELL', quantity)  # Создание рыночного ордера на продажу
            trade = ib.placeOrder(contract, order)  # Размещение ордера
            ib.cancelHistoricalData(bars)  # Отмена запроса исторических данных
            trades_count += 1  # Увеличение количества сделок
            profitable_trades += 1  # Увеличение количества прибыльных сделок
            capital_growth += quantity * (take_profit_price - entry_price)  # Увеличение прироста капитала
            return  # Выход из функции
       
        # Проверка на достижение уровня частичного тейк-профита
        if current_price >= partial_take_profit_price:
            partial_quantity = int(quantity * 0.7)  # Расчет количества акций для частичной продажи
            order = MarketOrder('SELL', partial_quantity)  # Создание рыночного ордера на частичную продажу
            trade = ib.placeOrder(contract, order)  # Размещение ордера
       
        # Проверка на достижение уровня стоп-лосса
        if current_price <= stop_loss_price:
            stop_loss_count[0] += 1  # Увеличение счетчика касаний стоп-лосса
            if stop_loss_count[0] == 2:  # Если стоп-лосс достигнут второй раз
                order = MarketOrder('SELL', quantity)  # Создание рыночного ордера на продажу
                trade = ib.placeOrder(contract, order)  # Размещение ордера
                ib.cancelHistoricalData(bars)  # Отмена запроса исторических данных
                trades_count += 1  # Увеличение количества сделок
                loss_trades += 1  # Увеличение количества убыточных сделок
                capital_growth -= quantity * (entry_price - stop_loss_price)  # Уменьшение капитала на величину убытка
       
        # Сброс счетчика стоп-лосса, если цена выше уровня стоп-лосса
        if current_price > stop_loss_price:
            stop_loss_count[0] = 0  # Сброс счетчика касаний стоп-лосса
       
    stop_loss_count = [0]  # Счетчик для второго касания стоп-лосса
    ib.barUpdateEvent += on_new_bar  # Привязка функции обработки новой свечи к событию

# Функция для проверки времени работы стратегии
def should_run():
    now = datetime.datetime.now(ny_tz).time()  # Получение текущего времени в часовом поясе Нью-Йорка
    return START_TIME <= now <= END_TIME  # Проверка, находится ли текущее время в пределах времени работы стратегии

# Функция для отправки уведомлений в Telegram
def send_telegram_notification(message):
    bot_token = '###################################################################'
    chat_id = '5118571426'  # Замените это на ваш фактический Chat ID
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    data = {'chat_id': chat_id, 'text': message}
    response = requests.post(url, data=data)
    if response.status_code == 200:
        print('Уведомление успешно отправлено')
    else:
        print(f'Ошибка отправки уведомления: {response.status_code} - {response.text}')

# Функция для отправки уведомления с результатами
def send_notification():
    global trades_count, profitable_trades, loss_trades, capital_growth
    message = (f"Всего сделок: {trades_count}, "
               f"Прибыльных: {profitable_trades}, "
               f"Убыточных: {loss_trades}, "
               f"Прирост капитала: {capital_growth} долларов")
    send_telegram_notification(message)

# Основной цикл работы стратегии
def run_strategy():
    while should_run():
        open_position('NYCB')  # Вызов функции для открытия позиции
        ib.sleep(60)  # Проверка каждую минуту

# Планирование работы стратегии
def job():
    today = datetime.datetime.now(ny_tz).date()
    # Получение расписаний всех трех бирж
    nyse = mcal.get_calendar('NYSE').schedule(start_date=today, end_date=today)
    nasdaq = mcal.get_calendar('NASDAQ').schedule(start_date=today, end_date=today)
    amex = mcal.get_calendar('AMEX').schedule(start_date=today, end_date=today)
   
    # Проверка, что сегодня рабочий день на любой из бирж
    if not nyse.empty or not nasdaq.empty or not amex.empty:
        run_strategy()
    else:
        print("Сегодня не рабочий день на бирже")

# Планирование ежедневного запуска стратегии
schedule.every().day.at("10:00").do(job)  # Запуск работы в 10:00 по Нью-Йорку

# Планирование ежедневного уведомления
schedule.every().day.at("16:00").do(send_notification)  # Уведомление в 16:00 по Нью-Йорку

# Запуск планировщика
while True:
    schedule.run_pending()
    time.sleep(1)
