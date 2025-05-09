import logging
import os
import re
import time
import random
from datetime import datetime, timedelta, time as dt_time
import asyncio
import openai
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
from flask import Flask
import threading
import pytz
import json
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
import fitz  # PyMuPDF per leggere PDF




# se False, non schedula mai i consigli
ENABLE_DAILY_TIPS = False

# Configura logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Chiavi API (da variabili ambiente)
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ASSISTANT_ID = os.getenv("ASSISTANT_ID")

openai.api_key = OPENAI_API_KEY
client = openai.OpenAI(api_key=OPENAI_API_KEY)

# Variabili globali
user_character_count = {}
user_message_count = {}
last_reset_date = datetime.now().date()
user_warnings = {}
user_blocked_until = {}
user_threads = {}
user_last_seen = {}
user_opt_in_daily_tips = {}
user_pdf_last_upload = {}


# Costanti
MAX_CHARACTERS_PER_DAY = 4000
MAX_MESSAGES_PER_DAY = 10
CHARACTER_WARNING_THRESHOLD = 500
MESSAGE_WARNING_THRESHOLD = 3
THREAD_EXPIRATION_DAYS = 7

# Frasi
OUT_OF_TOPIC_PHRASE = "Ciao, ricorda che mi occupo solo di domande legate alla finanza personale. Dimmi pure come posso aiutarti su questi argomenti"
THINKING_MESSAGES = [
    "Sto pensando alla miglior soluzione per te... 🧠💭",
    "Un attimo che rifletto sulla risposta migliore... 🤔",
    "Sto elaborando una risposta su misura per te... 📊",
    "Analizzo la tua domanda... un secondo! 🔎"
]
DAILY_TIPS = [
    "Ricorda di risparmiare almeno il 10% di ogni stipendio!",
    "Diversifica i tuoi investimenti per ridurre il rischio.",
    "Monitora le tue spese mensili per evitare sorprese.",
    "Investi prima in te stesso: formazione e competenze sono fondamentali.",
    "Costruisci un fondo di emergenza pari a 3-6 mesi di spese.",
    "Imposta obiettivi finanziari chiari e realistici.",
    "Evita debiti ad alto interesse come quelli delle carte di credito.",
    "Controlla regolarmente il tuo budget personale.",
    "Investi a lungo termine, non cercare guadagni rapidi.",
    "Approfitta dei piani pensionistici disponibili.",
    "Tieni separate le spese necessarie da quelle superflue.",
    "Cerca sempre di risparmiare su abbonamenti inutilizzati.",
    "Automatizza i tuoi risparmi per renderli costanti.",
    "Considera sempre i costi nascosti negli investimenti.",
    "Non investire mai denaro che non puoi permetterti di perdere.",
    "Pianifica per le emergenze mediche e familiari.",
    "Evita acquisti impulsivi: aspetta 24 ore prima di decidere.",
    "Usa liste della spesa per evitare spese inutili.",
    "Investi in formazione continua e aggiornamento professionale.",
    "Preferisci prodotti finanziari trasparenti e semplici.",
    "Crea più fonti di reddito se possibile.",
    "Evita di seguire le mode nei mercati finanziari.",
    "Non procrastinare: inizia a risparmiare oggi.",
    "Tieni traccia di tutte le tue entrate e uscite.",
    "Fissa un limite massimo di spesa mensile.",
    "Controlla periodicamente il tuo portafoglio investimenti.",
    "Non farti influenzare dalle emozioni nei tuoi investimenti.",
    "Mantieni la calma durante le fluttuazioni di mercato.",
    "Sii paziente: la ricchezza si costruisce nel tempo.",
    "Ogni piccolo risparmio oggi diventa un grande vantaggio domani."
]
DAILY_TIP_HEADERS = [
    "Consiglio del giorno 📈:",
    "Tip finanziario 💡:",
    "Suggerimento utile 🔥:",
    "Idea smart per te 💰:",
    "Spunto di oggi 📚:"
]

GPT_LINK = "https://chatgpt.com/g/g-680dee77df90819186bee2b1bb9dc48e-saveup-coach-gpt"
GPT_BUTTON = InlineKeyboardMarkup(
    [[InlineKeyboardButton("💬 Apri SaveUp Coach su ChatGPT", url=GPT_LINK)]]
)


# Funzione per gestire PDF ricevuti
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    document = update.message.document

    today = datetime.now().date()
    last_upload = user_pdf_last_upload.get(user_id)

    if last_upload == today:
        await update.message.reply_text(
            "📄 Hai già inviato un documento oggi. Puoi inviarne solo uno al giorno.\n\n"
            "Per analisi illimitate e senza limiti, usa SaveUp Coach su ChatGPT!",
            reply_markup=GPT_BUTTON)
        
        return

    user_pdf_last_upload[user_id] = today

    await update.message.reply_text(
        f"📄 Documento ricevuto! Analizzerò solo i primi 2000 caratteri per darti una spiegazione essenziale.\n\n"
        "Per analisi completa e senza limiti, puoi usare SaveUp Coach su ChatGPT", reply_markup=GPT_BUTTON
    )

    file = await context.bot.get_file(document.file_id)
    file_path = f"temp_{document.file_name}"
    await file.download_to_drive(file_path)

    try:
        text = extract_text_from_pdf(file_path)
        if len(text.strip()) < 50:
            await update.message.reply_text("Il documento sembra vuoto o illeggibile.", reply_markup=GPT_BUTTON)
            return

        prompt = (
            "Spiega le caratteristiche principali del documento in maniera semplice e chiara e aggiungi che consigli di utilizzare il gpts Save Up Coach su chat gpt:\n\n"
            + text[:2000]
        )
        response = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500
        )

        summary = response.choices[0].message.content.strip()
        await update.message.reply_text(f"Ecco un riassunto:\n\n{summary}", reply_markup=GPT_BUTTON)

    except Exception as e:
        logging.error(f"Errore elaborando PDF: {e}")
        await update.message.reply_text("C'è stato un errore nell'elaborazione del PDF.", reply_markup=GPT_BUTTON)
    finally:
        os.remove(file_path)

def extract_text_from_pdf(file_path):
    doc = fitz.open(file_path)
    text = ""
    for page in doc:
        text += page.get_text()
    doc.close()
    return text


# Funzione di benvenuto
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await save_user_id(update, context)
    user_id = update.message.from_user.id

    welcome_message = (
        "Ciao! Sono SaveUp Coach, il tuo assistente personale per la finanza! 📈\n\n"
        "Con me puoi:\n"
        "• Fare domande di finanza personale per muovere i primi passi nella gestione dei tuoi risparmi 🧠\n"
        "• Ricevere ogni sera la rassegna stampa con le notizie economiche del giorno 🗞️\n"
        "• Capire meglio i documenti informativi che ti sembrano troppo complessi 📄\n\n"
        "📌 *Disclaimer:* non fornisco consigli di investimento. Le informazioni fornite, anche se estratte da documenti, vanno sempre verificate con fonti ufficiali o con un professionista.\n\n"
        "Puoi anche usare SaveUp Coach su ChatGPT per un'esperienza completa!"
    )
    await update.message.reply_text(welcome_message, reply_markup=GPT_BUTTON)
    

    # SOLO se ho daily tips abilitati, chiedo l’opt-in    
    if ENABLE_DAILY_TIPS and user_id not in user_opt_in_daily_tips:
            await update.message.reply_text(
                "Vuoi ricevere ogni giorno alle 10:00 un consiglio di educazione finanziaria? 📈\n\n"
                "Rispondi semplicemente 'SI' oppure 'NO'."
            )

# Gestione messaggi liberi
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    user_message = update.message.text
    await save_user_id(update, context)

    global last_reset_date


    # Check se l'utente è bloccato
    if user_id in user_blocked_until and datetime.now() < user_blocked_until[user_id]:
        await update.message.reply_text("Sei stato temporaneamente bloccato per superamento limiti. Riprovaci più tardi! 🚫", reply_markup=GPT_BUTTON)
        return

    # Reset giornaliero
    if datetime.now().date() != last_reset_date:
        user_message_count.clear()
        user_character_count.clear()
        user_blocked_until.clear()
        last_reset_date = datetime.now().date()

    # Inizializza contatori se necessario
    if user_id not in user_message_count:
        user_message_count[user_id] = 0
    if user_id not in user_character_count:
        user_character_count[user_id] = 0

    # Aggiorna i contatori
    user_message_count[user_id] += 1
    user_character_count[user_id] += len(user_message)

    # Controlli e avvisi
    remaining_messages = MAX_MESSAGES_PER_DAY - user_message_count[user_id]
    remaining_characters = MAX_CHARACTERS_PER_DAY - user_character_count[user_id]

    if remaining_messages == MESSAGE_WARNING_THRESHOLD:
        await update.message.reply_text(
            f"Attenzione! Ti rimangono solo {remaining_messages} messaggi per oggi. 📩\n\n"
            "Puoi continuare a usare SaveUp Coach anche su ChatGPT!", reply_markup=GPT_BUTTON
        )

    if remaining_characters <= CHARACTER_WARNING_THRESHOLD:
        await update.message.reply_text(
            f"Attenzione! Ti rimangono solo {remaining_characters} caratteri disponibili oggi. ✍️\n\n"
            "Puoi continuare a usare SaveUp Coach anche su ChatGPT!", reply_markup=GPT_BUTTON
        )

    # Se ha superato i limiti
    if user_message_count[user_id] > MAX_MESSAGES_PER_DAY or user_character_count[user_id] > MAX_CHARACTERS_PER_DAY:
        user_blocked_until[user_id] = datetime.now() + timedelta(hours=24)
        await update.message.reply_text("Hai superato i limiti giornalieri. Sei bloccato per 24 ore. 🚫\n\n"
                                        "Puoi continuare a usare SaveUp Coach anche su ChatGPT!", reply_markup=GPT_BUTTON
        )
        return

    try:
        if user_id not in user_opt_in_daily_tips:
            if user_message.lower() == "si":
                user_opt_in_daily_tips[user_id] = True
                await update.message.reply_text("Perfetto! 🚀 Da oggi riceverai un consiglio ogni giorno alle 10:00.")
                return
            elif user_message.lower() == "no":
                user_opt_in_daily_tips[user_id] = False
                await update.message.reply_text("Nessun problema! 💬 Puoi cambiare idea in futuro se vuoi.")
                return

        if user_id not in user_threads:
            thread = client.beta.threads.create()
            user_threads[user_id] = thread.id

        thinking = random.choice(THINKING_MESSAGES)
        await update.message.reply_text(thinking)

        client.beta.threads.messages.create(
            thread_id=user_threads[user_id],
            role="user",
            content=user_message
        )

        run = client.beta.threads.runs.create(
            thread_id=user_threads[user_id],
            assistant_id=ASSISTANT_ID,
            instructions="Rispondi in massimo 700 caratteri, in modo chiaro, solo su temi di finanza personale."
        )

        while True:
            run_status = client.beta.threads.runs.retrieve(
                thread_id=user_threads[user_id],
                run_id=run.id
            )
            if run_status.status == "completed":
                break
            time.sleep(1)

        messages = client.beta.threads.messages.list(thread_id=user_threads[user_id])
        reply = messages.data[0].content[0].text.value

        await update.message.reply_text(reply)

    except Exception as e:
        logging.error(f"Errore durante risposta: {e}")
        await update.message.reply_text("Mi dispiace, non riesco a rispondere al momento. Riprova più tardi!", reply_markup=GPT_BUTTON)

# Invio consigli giornalieri
async def send_daily_tips(context: ContextTypes.DEFAULT_TYPE):
    for user_id, opted_in in user_opt_in_daily_tips.items():
        if opted_in:
            tip_header = random.choice(DAILY_TIP_HEADERS)
            tip_content = random.choice(DAILY_TIPS)
            try:
                await context.bot.send_message(chat_id=user_id, text=f"{tip_header}\n{tip_content}")
            except Exception as e:
                logging.error(f"Errore inviando consiglio a {user_id}: {e}")

# Invio link giornaliero
async def send_daily_link(context: ContextTypes.DEFAULT_TYPE):
    # path al file immagine (può essere locale o un URL)
    image_path = os.path.join(os.path.dirname(__file__), "images", "Immagine rassegna stampa.png")
    # testo del caption
    caption = "📰 La Rassegna stampa è pronta!"
    # inline button “Leggila ora”
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("Leggi ora", url="https://saveupnews.github.io/saveupnews/")]]
    )

    # carica la lista di user_id
    try:
        with open("user_ids.json", "r") as f:
            user_ids = json.load(f)
    except FileNotFoundError:
        user_ids = []

    for user_id in user_ids:
        try:
            # invia foto + caption + inline button
            await context.bot.send_photo(
                chat_id=user_id,
                photo=open(image_path, "rb"),
                caption=caption,
                reply_markup=keyboard
            )
        except Exception as e:
            logging.error(f"Errore inviando link a {user_id}: {e}")


# Salvataggio utenti

async def save_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        with open("user_ids.json", "r") as f:
            user_ids = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        user_ids = []

    if user_id not in user_ids:
        user_ids.append(user_id)
        with open("user_ids.json", "w") as f:
            json.dump(user_ids, f)
        logging.info(f"Nuovo user_id registrato: {user_id}")


# Comando per testare l'invio del link
async def test_send_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧪 Invio link in corso…")
    await send_daily_link(context)

app_web = Flask('')

@app_web.route('/')
def home():
    return "SaveUp Coach è online!"

def run():
    app_web.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = threading.Thread(target=run)
    t.start()

# --- Comando /iscritti ---
async def iscritti(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        with open("user_ids.json", "r") as f:
            user_ids = json.load(f)
        await update.message.reply_text(f"📊 Utenti iscritti: {len(user_ids)}")
    except FileNotFoundError:
        await update.message.reply_text("Nessun utente iscritto trovato.")

# Funzione principale
def main():
    keep_alive()
    
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(CommandHandler("prova_link", test_send_link))
    app.add_handler(CommandHandler("iscritti", iscritti))


    rome_tz = pytz.timezone("Europe/Rome")

    app.job_queue.run_daily(send_daily_link, time=dt_time(hour=18, minute=30, tzinfo=rome_tz))
    if ENABLE_DAILY_TIPS:
            app.job_queue.run_daily(send_daily_tips, time=dt_time(hour=10, minute=00, tzinfo=rome_tz))


    app.run_polling()

if __name__ == "__main__":
    main()
