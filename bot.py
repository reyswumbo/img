from __future__ import annotations

import asyncio
import html
import logging
import os
import random
import re
import shutil
import string
import subprocess
import uuid
from pathlib import Path

from telegram import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

BASE = Path(__file__).resolve().parent
DIR_BW = BASE / "hitam-putih"
DIR_COLOR = BASE / "color"
TMP_DIR = BASE / ".tmp_uploads"
BRANCH = os.environ.get("GIT_BRANCH", "main")

MENU, WAIT_IMAGE, WAIT_NAME = range(3)

BTN_BW = "\U0001F5A4 Hitam-Putih"
BTN_COLOR = "\U0001F3A8 Coloring"
BTN_LIST = "\U0001F4CB List Upload"
BTN_VIEW = "\U0001F441\uFE0F Lihat View"
BTN_PUSH = "\u2B06\uFE0F Push GitHub"
BTN_HOME = "\U0001F3E0 Menu"

IMG_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".heic")

COMMIT_WORDS = ["coloring", "sketsa"]

menu_kb = ReplyKeyboardMarkup(
    [[BTN_BW, BTN_COLOR], [BTN_LIST, BTN_VIEW], [BTN_PUSH], [BTN_HOME]],
    resize_keyboard=True,
)
home_kb = ReplyKeyboardMarkup([[BTN_HOME]], resize_keyboard=True)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s - %(message)s", level=logging.INFO
)
log = logging.getLogger("imgbot")


def get_token() -> str | None:
    token = os.environ.get("BOT_TOKEN")
    if token:
        return token.strip()
    env = BASE / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("BOT_TOKEN="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    token_file = Path("/root/tokentelegram.txt")
    if token_file.exists():
        content = token_file.read_text(encoding="utf-8").strip()
        if content:
            return content
    return None


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024


def sanitize_filename(name: str) -> str:
    name = name.strip().replace(" ", "-")
    name = re.sub(r"[^A-Za-z0-9._\-]", "", name)
    name = re.sub(r"\.{2,}", ".", name).lstrip(".")
    return name[:80]


def ensure_ext(filename: str, original: str) -> str:
    if Path(filename).suffix:
        return filename
    orig_ext = Path(original).suffix or ".jpg"
    return filename + orig_ext.lower()


def unique_path(folder: Path, filename: str) -> Path:
    target = folder / filename
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for i in range(1, 1000):
        candidate = folder / f"{stem}-{i}{suffix}"
        if not candidate.exists():
            return candidate
    return folder / f"{stem}-{uuid.uuid4().hex[:6]}{suffix}"


def list_files_in(folder: Path) -> list[Path]:
    return sorted(p for p in folder.glob("*") if p.is_file()) if folder.exists() else []


def github_urls(kind: str, name: str) -> tuple[str, str]:
    slug = get_repo_slug()
    sub = "color" if kind == "color" else "hitam-putih"
    web = f"https://github.com/{slug}/blob/{BRANCH}/{sub}/{name}"
    raw = f"https://raw.githubusercontent.com/{slug}/{BRANCH}/{sub}/{name}"
    return web, raw


def build_view_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for kind, folder in (("bw", DIR_BW), ("color", DIR_COLOR)):
        files = list_files_in(folder)
        for idx, p in enumerate(files):
            label = f"{folder.name}/{p.name}"
            if len(label) > 40:
                label = label[:37] + "..."
            rows.append(
                [InlineKeyboardButton(text=label, callback_data=f"view|{kind}|{idx}")]
            )
    if not rows:
        return None
    return InlineKeyboardMarkup(rows)


async def show_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    kb = build_view_keyboard()
    if kb is None:
        await update.message.reply_text(
            "\U0001F4C2 Belum ada gambar terupload. "
            "Upload dulu lewat \U0001F5A4 Hitam-Putih atau \U0001F3A8 Coloring.",
            reply_markup=menu_kb,
        )
        return MENU
    total = sum(len(r) for r in kb.inline_keyboard)
    await update.message.reply_text(
        f"\U0001F441\uFE0F Pilih gambar untuk melihat view ({total} file).\n"
        "URL GitHub aktif setelah gambar di-push.",
        reply_markup=kb,
    )
    return MENU


async def view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        _, kind, idx = query.data.split("|")
        idx = int(idx)
        folder = DIR_COLOR if kind == "color" else DIR_BW
        path = list_files_in(folder)[idx]
    except (ValueError, IndexError):
        await query.answer("File tidak ditemukan, coba buka menu lagi.", show_alert=True)
        return

    await query.answer()
    web_url, raw_url = github_urls(kind, path.name)
    caption = (
        f"\U0001F5BC {path.name}\n\n"
        f"\U0001F517 Full view:\n{web_url}\n\n"
        f"\U0001F4CB Raw URL:\n{raw_url}"
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(text="\U0001F517 Lihat Full View", url=web_url)],
            [
                InlineKeyboardButton(
                    text="\U0001F4CB Salin URL",
                    copy_text=CopyTextButton(text=raw_url),
                )
            ],
            [InlineKeyboardButton(text="\u2B05\uFE0F Kembali", callback_data="viewback")],
        ]
    )
    with open(path, "rb") as fh:
        try:
            await context.bot.send_photo(
                chat_id=query.message.chat_id, photo=fh, caption=caption, reply_markup=kb
            )
        except Exception:
            fh.seek(0)
            await context.bot.send_document(
                chat_id=query.message.chat_id, document=fh, caption=caption, reply_markup=kb
            )


async def view_back(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    kb = build_view_keyboard()
    if kb is None:
        await query.edit_message_text("Folder kosong.")
        return
    await query.edit_message_reply_markup(reply_markup=None)
    total = sum(len(r) for r in kb.inline_keyboard)
    await context.bot.send_message(
        query.message.chat_id,
        f"\U0001F441\uFE0F Pilih gambar untuk melihat view ({total} file).",
        reply_markup=kb,
    )


def gen_commit_msg() -> str:
    w1 = random.choice(COMMIT_WORDS)
    w2 = "".join(random.choices(string.ascii_lowercase, k=random.randint(6, 7)))
    return f"{w1}_{w2}"


def rand_token(k: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=k))


def build_target(folder: Path, chosen: str, kind: str) -> Path:
    tag = "_color" if kind == "color" else "_blackwhite"
    p = Path(chosen)

    def name_with(mid: str = "") -> str:
        return f"{p.stem}{mid}{tag}{p.suffix}"

    target = folder / name_with()
    while target.exists():
        target = folder / name_with(f"_{rand_token()}")
    return target


def get_repo_slug() -> str:
    url = run_git(["config", "--get", "remote.origin.url"]).stdout.strip()
    m = re.search(r"[:/]([\w.\-]+)/([\w.\-]+?)(?:\.git)?/?$", url)
    return f"{m.group(1)}/{m.group(2)}" if m else ""


GIT_TIMEOUT = 180


def run_git(args: list[str]) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["GIT_SSH_COMMAND"] = (
        "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new"
    )
    try:
        return subprocess.run(
            ["git", *args],
            cwd=BASE,
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args, 124, "", f"timeout: perintah lebih dari {GIT_TIMEOUT} detik"
        )


def code_block(text: str, limit: int = 1500) -> str:
    return f"<pre>{html.escape(text.strip()[-limit:])}</pre>"


def do_push() -> str:
    run_git(["add", "-A"])
    status = run_git(["status", "--porcelain"]).stdout.strip()
    if not status:
        return "\u2139\uFE0F Tidak ada perubahan baru untuk di-push."
    msg = gen_commit_msg()
    commit = run_git(["commit", "-m", msg])
    if commit.returncode != 0:
        return f"\u274C Commit gagal:\n{code_block(commit.stderr)}"
    push = run_git(["push", "origin", BRANCH])
    if push.returncode != 0:
        err = (push.stderr or push.stdout).strip()
        return (
            f"\u274C Push gagal:\n{code_block(err)}\n\n"
            "Pastikan SSH key GitHub tersedia di server ini."
        )

    head = run_git(["log", "-1", "--pretty=format:%h%n%s%n%ci"]).stdout.splitlines()
    sha, subj, date = head[0], head[1], head[2]
    shortstat = run_git(["show", "--shortstat", "--format="]).stdout.strip()
    files = run_git(
        ["diff-tree", "--no-commit-id", "--name-status", "-r", "HEAD"]
    ).stdout.strip()

    info = (
        f"commit  : {sha}\n"
        f"pesan   : {subj}\n"
        f"waktu   : {date}\n"
        f"{shortstat or '(detail tidak tersedia)'}\n"
        f"\n--- file berubah ---\n{files}"
    )
    return (
        f"\U0001F680 <b>Push berhasil ke origin/{BRANCH}</b>\n\n"
        f"{code_block(info)}"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "\U0001F5BC <b>IMG BOT</b> \U0001F44B\n"
        "\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n\n"
        "\U0001F4C2 <b>Upload Gambar</b>\n"
        "\u2022 \U0001F5A4 Hitam-Putih \u2192 <code>hitam-putih/</code>\n"
        "\u2022 \U0001F3A8 Coloring \u2192 <code>color/</code>\n"
        "<i>Nama file bebas, ekstensi otomatis menyesuaikan.\n"
        "Contoh: ketik</i> <code>1</code> <i>\u2192 tersimpan</i> <code>1_color.png</code>\n\n"
        "\u26A1 <b>Fitur</b>\n"
        "\U0001F4CB List Upload \u2014 daftar semua file\n"
        "\U0001F441\uFE0F Lihat View \u2014 gambar + URL GitHub + tombol salin\n"
        "\u2B06\uFE0F Push GitHub \u2014 commit acak + push otomatis\n\n"
        "\U0001F4A1 Ketik /help untuk panduan lengkap.",
        parse_mode="HTML",
        reply_markup=menu_kb,
    )
    return MENU


async def pick_folder(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    choice = update.message.text
    if choice == BTN_BW:
        context.user_data["folder"] = str(DIR_BW)
        context.user_data["kind"] = "bw"
        label = "hitam-putih/"
    else:
        context.user_data["folder"] = str(DIR_COLOR)
        context.user_data["kind"] = "color"
        label = "color/"
    await update.message.reply_text(
        f"Folder tujuan: {label}\n\nSekarang kirim gambarnya "
        "(foto atau file/dokumen gambar).\nTekan \U0001F3E0 Menu untuk batal.",
        reply_markup=home_kb,
    )
    return WAIT_IMAGE


async def receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if doc is not None:
        fname = (doc.file_name or "").lower()
        if not (doc.mime_type or "").startswith("image/") and not fname.endswith(IMG_EXTS):
            await update.message.reply_text(
                "\u26D4 Itu bukan gambar. Kirim foto atau file gambar ya."
            )
            return WAIT_IMAGE
        tg_file = await doc.get_file()
        orig_name = doc.file_name or f"file_{uuid.uuid4().hex[:6]}.jpg"
    else:
        photo = update.message.photo[-1]
        tg_file = await photo.get_file()
        orig_name = f"photo_{uuid.uuid4().hex[:6]}.jpg"

    TMP_DIR.mkdir(exist_ok=True)
    tmp_path = TMP_DIR / f"{uuid.uuid4().hex}_{Path(orig_name).name}"
    await tg_file.download_to_drive(custom_path=str(tmp_path))

    context.user_data["tmp_path"] = str(tmp_path)
    context.user_data["orig_name"] = orig_name

    await update.message.reply_text(
        f"\u2705 Gambar diterima ({fmt_size(tmp_path.stat().st_size)}).\n"
        f"Nama file saat ini: {orig_name}\n\n"
        "Kirim nama baru untuk file ini, atau tekan tombol di bawah "
        "untuk memakai nama sekarang.",
        reply_markup=ReplyKeyboardMarkup(
            [[f"\u2714\uFE0F Pakai: {orig_name}"], [BTN_HOME]],
            resize_keyboard=True,
        ),
    )
    return WAIT_NAME


async def save_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    orig = context.user_data.get("orig_name", "")
    tmp_path = context.user_data.get("tmp_path")
    folder_str = context.user_data.get("folder")

    if not tmp_path or not Path(tmp_path).exists():
        await update.message.reply_text(
            "\u26A0\uFE0F Sesi upload hangus, silakan ulangi.", reply_markup=menu_kb
        )
        return MENU

    chosen = orig if text.startswith("\u2714\uFE0F") else text
    chosen = sanitize_filename(chosen)
    if not chosen:
        await update.message.reply_text(
            "\u26D4 Nama file tidak valid, coba nama lain."
        )
        return WAIT_NAME
    chosen = ensure_ext(chosen, orig)

    folder = Path(folder_str or DIR_BW)
    folder.mkdir(exist_ok=True)
    target = build_target(folder, chosen, context.user_data.get("kind", "bw"))
    shutil.move(tmp_path, target)
    context.user_data.clear()

    await update.message.reply_text(
        f"\U0001F4BE Tersimpan: {folder.name}/{target.name}\n\n"
        "Mau upload lagi? Pilih folder tujuan, lihat \U0001F4CB List Upload, "
        "atau langsung \u2B06\uFE0F Push GitHub.",
        reply_markup=menu_kb,
    )
    return MENU


async def show_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lines = []
    total = 0
    total_size = 0
    for label, folder in (("hitam-putih", DIR_BW), ("color", DIR_COLOR)):
        files = sorted(p for p in folder.glob("*") if p.is_file()) if folder.exists() else []
        lines.append(f"\U0001F4C1 {label}/ ({len(files)} file)")
        if not files:
            lines.append("   \u2514 (kosong)")
        else:
            for i, p in enumerate(files, 1):
                size = p.stat().st_size
                total_size += size
                lines.append(f"   {i}. {p.name} ({fmt_size(size)})")
        total += len(files)
        lines.append("")
    lines.append(f"\U0001F5C2 Total: {total} file ({fmt_size(total_size)})")
    await update.message.reply_text("\n".join(lines), reply_markup=menu_kb)
    return MENU


async def push_github(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    wait = await update.message.reply_text(
        "\u23F3 Sedang push ke GitHub...", reply_markup=home_kb
    )
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(do_push), timeout=GIT_TIMEOUT + 30
        )
    except Exception as e:
        result = f"\u274C Terjadi kesalahan: {html.escape(str(e))}"
    try:
        await wait.delete()
    except Exception:
        pass
    await update.message.reply_text(
        result, parse_mode="HTML", reply_markup=menu_kb
    )
    return MENU


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "\u2139\uFE0F Cara pakai:\n"
        "1. \U0001F5A4 / \U0001F3A8 pilih folder tujuan\n"
        "2. Kirim gambar (foto atau dokumen)\n"
        "3. Ketik nama file baru atau pakai nama bawaan\n"
        "   (otomatis jadi nama_color.png / nama_blackwhite.jpg)\n"
        "   (nama duplikat otomatis diberi acak: 1_xkzpq_color.png)\n"
        "4. \U0001F4CB List Upload \u2192 lihat semua file\n"
        "5. \U0001F441\uFE0F Lihat View \u2192 gambar + URL GitHub + tombol salin\n"
        "6. \u2B06\uFE0F Push GitHub \u2192 commit acak + push otomatis",
        reply_markup=menu_kb,
    )


async def go_home(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("tmp_path", None)
    await update.message.reply_text(
        "Kembali ke menu utama.", reply_markup=menu_kb
    )
    return MENU


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    tmp = context.user_data.pop("tmp_path", None)
    if tmp and Path(tmp).exists():
        Path(tmp).unlink(missing_ok=True)
    await update.message.reply_text("Dibatalkan. Sampai jumpa!", reply_markup=menu_kb)
    return MENU


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("Exception:", exc_info=context.error)


def build_app() -> Application:
    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND
                & filters.Regex(f"^({BTN_BW}|{BTN_COLOR})$"),
                pick_folder,
            ),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(f"^{BTN_LIST}$"),
                show_list,
            ),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(f"^{BTN_VIEW}$"),
                show_view,
            ),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(f"^{BTN_PUSH}$"),
                push_github,
            ),
        ],
        states={
            MENU: [
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND
                    & filters.Regex(f"^({BTN_BW}|{BTN_COLOR})$"),
                    pick_folder,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & filters.Regex(f"^{BTN_LIST}$"),
                    show_list,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & filters.Regex(f"^{BTN_VIEW}$"),
                    show_view,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & filters.Regex(f"^{BTN_PUSH}$"),
                    push_github,
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & filters.Regex(f"^{BTN_HOME}$"),
                    go_home,
                ),
            ],
            WAIT_IMAGE: [
                MessageHandler(
                    filters.PHOTO | filters.Document.IMAGE, receive_image
                ),
                MessageHandler(
                    filters.TEXT & ~filters.COMMAND & filters.Regex(f"^{BTN_HOME}$"),
                    go_home,
                ),
            ],
            WAIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, save_file),
            ],
        },
        fallbacks=[
            CommandHandler("start", cmd_start),
            CommandHandler("cancel", cancel_cmd),
            CommandHandler("help", help_cmd),
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(f"^{BTN_HOME}$"),
                go_home,
            ),
        ],
        allow_reentry=True,
    )

    app = Application.builder().token(get_token()).build()
    app.add_handler(conv)
    app.add_handler(CallbackQueryHandler(view_callback, pattern=r"^view\|"))
    app.add_handler(CallbackQueryHandler(view_back, pattern=r"^viewback$"))
    app.add_error_handler(error_handler)
    return app


def main() -> None:
    token = get_token()
    if not token:
        raise SystemExit(
            "BOT_TOKEN tidak ditemukan.\n"
            "Set lewat environment variable BOT_TOKEN, file .env berisi "
            "BOT_TOKEN=..., atau file /root/tokentelegram.txt."
        )
    for d in (DIR_BW, DIR_COLOR, TMP_DIR):
        d.mkdir(exist_ok=True)
    for leftover in TMP_DIR.glob("*"):
        leftover.unlink(missing_ok=True)

    log.info("Bot mulai berjalan (folder: %s)", BASE)
    build_app().run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
