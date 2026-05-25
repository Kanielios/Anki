import os
import csv
from io import StringIO
from pathlib import Path
import secrets
from dotenv import load_dotenv
from flask import Flask, Response, render_template, request, jsonify, redirect, url_for, flash
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from groq import Groq
import database as db
from models import Rating

env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

api_key = os.environ.get("GROQ_API_KEY")
client = Groq(api_key=api_key) if api_key else None

db.init_db()
login_manager = LoginManager(app)
login_manager.login_view = "login"

@login_manager.user_loader
def load_user(user_id): return db.get_user(int(user_id))

# --- АВТОРИЗАЦИЯ ---
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated: return redirect(url_for("index"))
    if request.method == "POST":
        user = db.get_user_by_username(request.form.get("username", "").strip())
        if user and check_password_hash(user.password_hash, request.form.get("password", "")):
            login_user(user)
            return redirect(url_for("index"))
        flash("Неверный логин или пароль")
    return render_template("login.html", is_register=False)

@app.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated: return redirect(url_for("index"))
    if request.method == "POST":
        username, password = request.form.get("username", "").strip(), request.form.get("password", "")
        if len(username) < 3: flash("Логин слишком короткий")
        elif len(password) < 6: flash("Пароль должен быть от 6 символов")
        elif db.get_user_by_username(username): flash("Логин занят")
        else:
            db.create_user(username, generate_password_hash(password))
            flash("Регистрация успешна! Войдите.")
            return redirect(url_for("login"))
    return render_template("login.html", is_register=True)

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# --- СТРАНИЦЫ ---
@app.route("/")
@login_required
def index():
    decks = db.get_decks(current_user.id)
    decks_data = [{"deck": d, "stats": db.get_deck_stats(current_user.id, d.id)} for d in decks]
    heatmap = db.get_activity_heatmap(current_user.id)
    leeches = db.get_leeches(current_user.id)
    return render_template("index.html", decks_data=decks_data, heatmap=heatmap, leeches=leeches)

@app.route("/decks/new", methods=["POST"])
@login_required
def new_deck():
    name = request.form.get("name", "").strip()
    if name:
        try:
            db.create_deck(current_user.id, name, request.form.get("description", "").strip())
            flash("Колода создана")
        except Exception:
            flash("Ошибка: колода с таким именем уже есть")
    return redirect(url_for("index"))

@app.route("/decks/<int:deck_id>/delete", methods=["POST"])
@login_required
def delete_deck(deck_id):
    db.delete_deck(current_user.id, deck_id)
    return redirect(url_for("index"))

@app.route("/decks/<int:deck_id>")
@login_required
def deck_detail(deck_id):
    deck = db.get_deck(current_user.id, deck_id)
    if not deck: return redirect(url_for("index"))
    return render_template("deck.html", deck=deck, cards=db.get_cards_for_deck(current_user.id, deck_id), stats=db.get_deck_stats(current_user.id, deck_id))


@app.route("/decks/<int:deck_id>/settings", methods=["POST"])
@login_required
def update_deck_settings(deck_id):
    deck = db.get_deck(current_user.id, deck_id)
    if not deck:
        flash("Колода не найдена")
        return redirect(url_for("index"))

    try:
        deck.new_per_day = max(0, min(200, int(request.form.get("new_per_day", deck.new_per_day))))
        deck.review_per_day = max(0, min(1000, int(request.form.get("review_per_day", deck.review_per_day))))
        with db.db_session() as conn:
            conn.execute(
                "UPDATE decks SET description=?, new_per_day=?, review_per_day=? WHERE id=? AND user_id=?",
                (
                    request.form.get("description", "").strip(),
                    deck.new_per_day,
                    deck.review_per_day,
                    deck.id,
                    current_user.id,
                ),
            )
        flash("Настройки колоды обновлены")
    except ValueError:
        flash("Лимиты должны быть числами")
    return redirect(url_for("deck_detail", deck_id=deck_id))

@app.route("/decks/<int:deck_id>/cards/new", methods=["POST"])
@login_required
def new_card(deck_id):
    front, back = request.form.get("front", "").strip(), request.form.get("back", "").strip()
    if front and back:
        try:
            db.create_card(current_user.id, deck_id, front, back, request.form.get("tags", "").strip())
            flash("Карточка сохранена")
        except ValueError:
            flash("Колода не найдена")
    return redirect(url_for("deck_detail", deck_id=deck_id))

@app.route("/decks/<int:deck_id>/import", methods=["POST"])
@login_required
def import_csv(deck_id):
    if not db.get_deck(current_user.id, deck_id):
        flash("Колода не найдена")
        return redirect(url_for("index"))

    file = request.files.get("csv_file")
    filename = secure_filename(file.filename or "") if file else ""
    if file and filename.lower().endswith('.csv'):
        raw = file.stream.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1251")
        stream = StringIO(text, newline=None)
        reader = csv.reader(stream, delimiter=',')
        count = 0
        for row in reader:
            if len(row) >= 2:
                db.create_card(current_user.id, deck_id, row[0].strip(), row[1].strip(), row[2].strip() if len(row) > 2 else "")
                count += 1
        flash(f"Импортировано карточек: {count}")
    else:
        flash("Загрузите CSV-файл")
    return redirect(url_for("deck_detail", deck_id=deck_id))


@app.route("/decks/<int:deck_id>/export")
@login_required
def export_csv(deck_id):
    deck = db.get_deck(current_user.id, deck_id)
    if not deck:
        return redirect(url_for("index"))

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["front", "back", "tags"])
    for card in db.get_cards_for_deck(current_user.id, deck_id):
        writer.writerow([card.front, card.back, card.tags])

    filename = secure_filename(deck.name) or f"deck-{deck_id}"
    return Response(
        output.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}.csv"},
    )

@app.route("/cards/<int:card_id>/delete", methods=["POST"])
@login_required
def delete_card(card_id):
    db.delete_card(current_user.id, card_id)
    return redirect(request.referrer or url_for("index"))

@app.route("/cards/<int:card_id>/edit", methods=["POST"])
@login_required
def edit_card(card_id):
    card = db.get_card(current_user.id, card_id)
    if card:
        card.front, card.back, card.tags = request.form.get("front", "").strip(), request.form.get("back", "").strip(), request.form.get("tags", "").strip()
        db.save_card(card)
    return redirect(request.referrer or url_for("index"))

@app.route("/study/<int:deck_id>")
@login_required
def study(deck_id):
    deck = db.get_deck(current_user.id, deck_id)
    if not deck: return redirect(url_for("index"))
    cards = db.get_due_cards(current_user.id, deck_id)
    return render_template("study.html", deck=deck, total=len(cards), done=not cards)

# --- API ---
@app.route("/api/study/<int:deck_id>/next")
@login_required
def api_next_card(deck_id):
    if not db.get_deck(current_user.id, deck_id):
        return jsonify({"error": "Deck not found"}), 404
    cards = db.get_due_cards(current_user.id, deck_id, limit=1)
    if not cards: return jsonify({"done": True})
    return jsonify({
        "done": False,
        "card": {"id": cards[0].id, "front": cards[0].front, "back": cards[0].back},
        "hints": cards[0].next_intervals(),
        "remaining": len(db.get_due_cards(current_user.id, deck_id)),
    })

@app.route("/api/study/answer", methods=["POST"])
@login_required
def api_answer():
    data = request.get_json(silent=True) or {}
    card = db.get_card(current_user.id, data.get("card_id"))
    if not card: return jsonify({"error": "Access denied"}), 404
    try:
        rating = Rating(int(data.get("rating")))
        prev_state = {"state": int(card.state), "interval": card.interval, "ease": card.ease, "due": card.due, "reps": card.reps, "lapses": card.lapses, "learning_step": card.learning_step}
        card.answer(rating)
        db.save_card(card)
        db.log_study(current_user.id, card, int(rating), prev_state)
        return jsonify({"success": True})
    except (TypeError, ValueError):
        return jsonify({"error": "Bad rating"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/study/undo", methods=["POST"])
@login_required
def api_undo():
    card = db.undo_last_study(current_user.id)
    if not card:
        return jsonify({"error": "Nothing to undo"}), 404
    return jsonify({"success": True, "card_id": card.id})


@app.route("/api/ai/generate", methods=["POST"])
@login_required
def ai_generate():
    payload = request.get_json(silent=True) or {}
    word = payload.get("word", "").strip()
    if not client or not word:
        return jsonify({"error": "API не настроен или слово пустое"}), 400

    # Твой детализированный системный промпт
    system_prompt = (
        "Ты — ассистент по изучению иностранных языков. Твоя задача — проанализировать слово или фразу "
        "и выдать структурированную карточку.\n\n"
        "Формат ответа:\n"
        "📌 [2-3 Перевода]\n"
        "📊 Частотность: [Заполни шкалу ▓ на базе данных о частоте использования] (X/10) точно определи количество этих штучек\n"
        "📝 Стиль: [Нейтральный/Официальный/Сленг]\n"
        "••••••••••••••••••••\n"
        "💬 Примеры:\n"
        "[Пример 1 на английском с выделением слова]\n"
        "[Пример 2 на английском с выделением слова]\n"
        "••••••••••••••••••••\n"
        "🧩 Когда используется\n"
        "[Краткое пояснение контекста на русском]\n"
        "••••••••••••••••••••\n"
        "- Никаких вступительных фраз.\n"
        "- Строго соблюдай пунктирные линии.\n"
        "- Если слово имеет несколько значений, выбери самое частое."
    )

    try:
        resp = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Слово: {word}"}
            ],
            model="llama-3.1-8b-instant",  # Или llama3-70b-8192 для еще более точной статистики
            temperature=0.3,  # Понижаем температуру для строгого следования формату
        )
        return jsonify({"example": resp.choices[0].message.content})
    except Exception as e:
        print(f"Ошибка Groq: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/api/ai/explain", methods=["POST"])
@login_required
def ai_explain():
    data = request.get_json(silent=True) or {}
    if not client: return jsonify({"explanation": "⚠️ AI клиент не инициализирован."})
    try:
        resp = client.chat.completions.create(
            messages=[{"role": "system", "content": "Ты лингвистический помощник."},
                      {"role": "user", "content": f"Контекст: {data.get('context', '')}. Вопрос: {data.get('question')}"}],
            model="llama-3.1-8b-instant",
        )
        return jsonify({"explanation": resp.choices[0].message.content})
    except Exception as e: return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(port=5001, debug=True)
