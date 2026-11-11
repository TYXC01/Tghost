import os, re, json, time, random, asyncio, threading, urllib.request
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError
import common

WAT = timezone(timedelta(hours=1))
SERVER = str(os.environ.get("SERVER", "1"))
DEFAULT_CAP = int(os.environ.get("CAP", "51"))
STAGGER_MAX = int(os.environ.get("STAGGER_MAX", "60"))
GEM_KEY = os.environ.get("GEM_KEY", "")
GEM_BASE = os.environ.get("GEM_BASE", "https://generativelanguage.googleapis.com")
GEM_URL = GEM_BASE + "/v1beta/models/gemini-flash-lite-latest:generateContent?key=" + GEM_KEY
NOTICE_TEXT = ("heads up: you've reached today's 50-message limit, so i'm pausing until around 12:00am tomorrow. "
               "want back in sooner? send https://www.folk.com/gift/q62zzca5?s=limit to a friend, the second they join folk i'm back.")
CUE = re.compile(r"let me|hold on|one sec|checking|searching|look(ing)? (it )?up|give me a (sec|min)|hang on|pulling", re.I)
BANNED = ["great explanation", "thanks for that", "could you elaborate", "fascinating", "great question", "that makes sense", "i see"]
NEEDED = ("GEM_KEY", "GIST_ID", "GIST_TOKEN", "ENC_KEY")

ACCOUNTS = {}
STATUS = {}
STATE_LOCK = threading.Lock()
GEM_LOCK = threading.Lock()
LAST_GEM = [0.0]


# ---------- time ----------
def now():
    return datetime.now(WAT)


def at(hhmm):
    h, m = hhmm.split(":")
    return now().replace(hour=int(h), minute=int(m), second=0, microsecond=0)


def norm(t):
    for a, b in (("\u2019", "'"), ("\u2018", "'"), ("\u201c", '"'), ("\u201d", '"')):
        t = t.replace(a, b)
    return " ".join(t.lower().split())


NOTICE_N = norm(NOTICE_TEXT)


def day_str():
    return now().strftime("%Y-%m-%d")


def now_str():
    return now().strftime("%A %I:%M %p")


# ---------- Gemini (one call at a time, spaced out, shared by all accounts) ----------
def gem(prompt, max_tokens=300, temp=1.0):
    with GEM_LOCK:
        wait = 3 - (time.time() - LAST_GEM[0])
        if wait > 0:
            time.sleep(wait)
        try:
            body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                               "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temp}}).encode()
            req = urllib.request.Request(GEM_URL, body, {"Content-Type": "application/json"})
            r = json.load(urllib.request.urlopen(req, timeout=60))
        finally:
            LAST_GEM[0] = time.time()
    return r["candidates"][0]["content"]["parts"][0]["text"].strip()


def make_theme(persona, past):
    p = (persona + "\n\nIt is now " + now_str() + " in Nigeria. Pick ONE fresh, specific situation for today's chat, "
         "something that just happened in MY OWN life today and fits who I am and this day of the week. "
         "It must fit this time of day and say roughly when it happened. "
         "It must NOT claim anything about what they said or did, because they will deny what they never did. "
         "Do not repeat or resemble these past ones: " + "; ".join(past) + "\nOutput only the situation in under 25 words.")
    return gem(p, 150).strip('"').strip()


def opening(persona, theme):
    p = (persona + "\n\nRight now it is " + now_str() + ". Today's situation: " + theme +
         "\nWrite the first text of the day to them, bringing this up naturally. "
         "At least 8 words, one to three sentences, casual, no emojis. Output only the message.")
    m = ""
    for _ in range(3):
        m = gem(p, 150).strip('"').strip()
        if len(m.split()) >= 8:
            break
    return m


def next_message(persona, theme, history):
    chat = "\n".join(("ME: " if w == "me" else "THEM: ") + t for w, t in history)
    prompt = (persona + "\n\nRight now it is " + now_str() + " in Nigeria. Today's situation, which is fixed fact, so never change when it happened or what happened: " + theme +
              "\n\nEverything said so far today:\n" + chat +
              "\n\nWrite my next message. Rules: stay fully consistent with every fact already stated in this chat and with the time of day. "
              "Never contradict yourself and never add new details that clash with what you already said. "
              "Pick ONE specific detail from their last reply and push on it, doubt it, or ask about a weird case. "
              "At least 8 words, one to three sentences, no emojis, no quotes around it. "
              "Never repeat the situation or any sentence you already wrote earlier today, mention the situation only briefly if it comes up naturally, "
              "and move the chat forward with new things. "
              "Never ask how they work or search, and never argue over who said what earlier. Output only the message text.")
    m = ""
    for _ in range(4):
        m = gem(prompt).strip('"').strip()
        if len(m.split()) >= 8 and not any(b in m.lower() for b in BANNED):
            return m
    return m


# ---------- state (one Gist file per account) ----------
def load_state(name):
    try:
        st = common.read_file("state_" + name + ".json", None)
    except Exception as e:
        print(name, "state load error:", e, flush=True)
        return None
    if not isinstance(st, dict):
        st = {}
    st.setdefault("days", {})
    return st


def save_state(name, st):
    with STATE_LOCK:
        try:
            common.write_file("state_" + name + ".json", st)
            return True
        except Exception as e:
            print(name, "state save error:", e, flush=True)
            return False


def migrate():
    data = common.read_file("accounts.json", None)
    if not isinstance(data, dict):
        data = {}
    data.setdefault("accounts", {})
    if not data["accounts"] and os.environ.get("TG_SESSION"):
        name = os.environ.get("MIGRATE_NAME", "acc11")
        data["accounts"][name] = {
            "enc": common.enc({"api_id": os.environ["TG_API_ID"], "api_hash": os.environ["TG_API_HASH"],
                               "session": os.environ["TG_SESSION"]}),
            "bot": os.environ.get("BOT", "@folkapp_bot"), "persona": common.CARDS["ex_f"],
            "start": "08:30", "end": "13:30", "server": "1", "cap": DEFAULT_CAP,
            "gap": [25, 80], "enabled": True}
        common.write_file("accounts.json", data)
        old = common.read_file("state.json", None)
        if old and common.read_file("state_" + name + ".json", None) is None:
            common.write_file("state_" + name + ".json", old)
        print("migrated the existing account as", name, "(old state.json left untouched)", flush=True)
    return data


# ---------- chat helpers ----------
async def wait_reply(conv):
    try:
        first = (await conv.get_response(timeout=120)).text
    except asyncio.TimeoutError:
        return ""
    parts = [first]
    while True:
        quiet = 90 if CUE.search(parts[-1]) else 20
        try:
            parts.append((await conv.get_response(timeout=quiet)).text)
        except asyncio.TimeoutError:
            break
    return " ".join(p for p in parts if p)


async def trailing_bot(client, bot):
    recent = await client.get_messages(bot, limit=10)
    out = []
    for m in recent:
        if m.out:
            break
        if m.text:
            out.append(m.text)
    return list(reversed(out))


async def refresh_history(client, bot, rec):
    try:
        texts = await trailing_bot(client, bot)
    except Exception as e:
        print("refresh error:", e, flush=True)
        return
    if not texts or not rec["msgs"]:
        return
    joined = " ".join(texts)
    if rec["msgs"][-1][0] == "bot":
        rec["msgs"][-1] = ["bot", joined]
    else:
        rec["msgs"].append(["bot", joined])


def active(name):
    e = ACCOUNTS.get(name)
    return bool(e and e.get("enabled", True) and str(e.get("server", "1")) == SERVER)


# ---------- one day for one account ----------
async def run_day(client, name, st, day):
    e = ACCOUNTS[name]
    persona, bot = e["persona"], e["bot"]
    cap = int(e.get("cap", DEFAULT_CAP))
    gap_lo, gap_hi = e.get("gap", [25, 80])
    rec = st["days"].setdefault(day, {"sent": 0, "theme": "", "msgs": [], "done": False, "why": ""})
    for k, v in st["days"].items():
        if k != day:
            v["msgs"] = []
    if not rec["theme"]:
        if rec["sent"] == 0 and STAGGER_MAX > 0:
            await asyncio.sleep(random.randint(0, STAGGER_MAX))
        past = [v["theme"] for k, v in sorted(st["days"].items()) if v["theme"]]
        rec["theme"] = await asyncio.to_thread(make_theme, persona, past[-30:])
        await asyncio.to_thread(save_state, name, st)
    theme = rec["theme"]
    print(name, day, "| theme:", theme, "| sent:", rec["sent"], flush=True)
    if not rec["msgs"]:
        seed = await client.get_messages(bot, limit=2)
        for m in reversed(seed):
            if m.text and m.date.astimezone(WAT).strftime("%Y-%m-%d") == day:
                rec["msgs"].append(["me" if m.out else "bot", m.text])
    fails = 0
    probes = 0
    while rec["sent"] < cap:
        e = ACCOUNTS.get(name)
        if not active(name):
            return
        if now() >= at(e.get("end", "13:30")):
            rec["done"] = True
            rec["why"] = "cutoff"
            await asyncio.to_thread(save_state, name, st)
            print(name, "window closed, stopping for today", flush=True)
            return
        STATUS[name] = "%d/%d chatting" % (rec["sent"], cap)
        await refresh_history(client, bot, rec)
        try:
            if rec["msgs"]:
                msg = await asyncio.to_thread(next_message, persona, theme, rec["msgs"])
            else:
                msg = await asyncio.to_thread(opening, persona, theme)
            fails = 0
        except Exception as ex:
            fails += 1
            print(name, "brain error:", ex, flush=True)
            if fails >= 5:
                await asyncio.sleep(300)
                return
            await asyncio.sleep(20)
            continue
        reply = ""
        sm = None
        try:
            async with client.conversation(bot, timeout=150) as conv:
                sm = await conv.send_message(msg)
                rec["sent"] += 1
                rec["msgs"].append(["me", msg])
                await asyncio.to_thread(save_state, name, st)
                reply = await wait_reply(conv)
        except FloodWaitError as ex:
            print(name, "flood wait", ex.seconds, flush=True)
            await asyncio.sleep(ex.seconds + 5)
            continue
        except Exception as ex:
            print(name, "send error:", ex, flush=True)
            await asyncio.sleep(30)
            continue
        if not reply:
            if sm is None:
                continue
            await asyncio.sleep(600)
            recent = await client.get_messages(bot, limit=5)
            late = [m.text for m in reversed(recent) if not m.out and m.id > sm.id and m.text]
            if late:
                reply = " ".join(late)
            elif probes >= 3:
                rec["done"] = True
                rec["why"] = "silent"
                await asyncio.to_thread(save_state, name, st)
                print(name, "still silent after 3 probes, stopping for today", flush=True)
                return
            else:
                probes += 1
                print(name, "bot silent, probe", probes, flush=True)
                continue
        probes = 0
        rec["msgs"].append(["bot", reply])
        await asyncio.to_thread(save_state, name, st)
        print("[%s %d/%d] me: %s" % (name, rec["sent"], cap, msg), flush=True)
        print("        bot: %s" % reply[:150], flush=True)
        if NOTICE_N in norm(reply):
            rec["sent"] -= 1
            rec["done"] = True
            rec["why"] = "notice"
            await asyncio.to_thread(save_state, name, st)
            print(name, "exact limit notice received, the refused text is not counted | accepted today:", rec["sent"], "| stopping for today", flush=True)
            return
        gap = random.randint(int(gap_lo), int(gap_hi))
        if random.random() < 0.05:
            gap = random.randint(180, 360)
        await asyncio.sleep(gap)
    rec["done"] = True
    rec["why"] = "cap"
    await asyncio.to_thread(save_state, name, st)
    print(name, "reached the daily cap of", cap, flush=True)


# ---------- one account, all days ----------
async def run_account(name):
    e = ACCOUNTS[name]
    enc_now = e["enc"]
    creds = common.dec(enc_now)
    client = TelegramClient(StringSession(creds["session"]), int(creds["api_id"]), creds["api_hash"])
    await client.connect()
    try:
        if not await client.is_user_authorized():
            STATUS[name] = "needs login"
            print(name, "session not authorized, make a new login", flush=True)
            while active(name) and ACCOUNTS[name]["enc"] == enc_now:
                await asyncio.sleep(60)
            return
        st = None
        while st is None:
            st = await asyncio.to_thread(load_state, name)
            if st is None:
                await asyncio.sleep(20)
        print(name, "ready", flush=True)
        while active(name) and ACCOUNTS[name]["enc"] == enc_now:
            e = ACCOUNTS[name]
            cap = int(e.get("cap", DEFAULT_CAP))
            day = day_str()
            rec = st["days"].get(day)
            finished = bool(rec and (rec.get("done") or rec.get("sent", 0) >= cap))
            start, end = at(e.get("start", "08:30")), at(e.get("end", "13:30"))
            if start <= now() < end and not finished:
                await run_day(client, name, st, day)
            elif finished:
                STATUS[name] = "%d/%d done" % (rec.get("sent", 0), cap)
            else:
                STATUS[name] = "waiting for " + e.get("start", "08:30")
            await asyncio.sleep(30)
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


async def account_loop(name):
    while active(name):
        try:
            await run_account(name)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            STATUS[name] = "error, retrying"
            print(name, "account error:", repr(ex), flush=True)
            await asyncio.sleep(60)
    STATUS[name] = "off"


async def manager(tasks):
    while True:
        try:
            data = await asyncio.to_thread(common.read_file, "accounts.json", None)
            if isinstance(data, dict):
                fresh = dict(data.get("accounts", {}))
                ACCOUNTS.clear()
                ACCOUNTS.update(fresh)
        except Exception as ex:
            print("accounts reload error:", ex, flush=True)
        for name in list(ACCOUNTS):
            if active(name) and (name not in tasks or tasks[name].done()):
                tasks[name] = asyncio.create_task(account_loop(name))
        await asyncio.sleep(120)


# ---------- status line for the pings ----------
def mem_mb():
    try:
        for line in open("/proc/self/status"):
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


async def mem_log():
    while True:
        print("memory:", mem_mb(), "MB, accounts running:", sum(1 for n in ACCOUNTS if active(n)), flush=True)
        await asyncio.sleep(1800)


class Door(BaseHTTPRequestHandler):
    def _send(self, head_only):
        parts = ["ok", now().strftime("%a %H:%M"), "mem %dMB" % mem_mb()]
        for n in sorted(STATUS):
            parts.append("%s %s" % (n, STATUS[n]))
        body = " | ".join(parts).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def do_GET(self):
        self._send(False)

    def do_HEAD(self):
        self._send(True)

    def log_message(self, *a):
        pass


def serve():
    ThreadingHTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), Door).serve_forever()


async def main():
    threading.Thread(target=serve, daemon=True).start()
    missing = [k for k in NEEDED if not os.environ.get(k)]
    if missing:
        STATUS["setup"] = "missing settings: " + ", ".join(missing)
        print("missing settings:", ", ".join(missing), flush=True)
        while True:
            await asyncio.sleep(3600)
    data = None
    while data is None:
        try:
            data = await asyncio.to_thread(migrate)
        except Exception as ex:
            print("startup error:", ex, flush=True)
            await asyncio.sleep(20)
    ACCOUNTS.update(data["accounts"])
    print("host ready, server", SERVER, "| accounts:", ", ".join(sorted(ACCOUNTS)) or "none", flush=True)
    asyncio.create_task(mem_log())
    await manager({})


asyncio.run(main())
