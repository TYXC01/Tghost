import os, re, json, time, random, asyncio, threading, urllib.request
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError

WAT = timezone(timedelta(hours=1))
BOT = os.environ.get("BOT", "@folkapp_bot")
CAP = int(os.environ.get("CAP", "100"))
TOTAL = 1000
START = (8, 30)
END = (13, 30)
GEM_KEY = os.environ["GEM_KEY"]
GIST_ID = os.environ["GIST_ID"]
GIST_TOKEN = os.environ["GIST_TOKEN"]
API_ID = int(os.environ["TG_API_ID"])
API_HASH = os.environ["TG_API_HASH"]
SESSION = os.environ["TG_SESSION"]
GEM_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-flash-lite-latest:generateContent?key=" + GEM_KEY
NOTICE = re.compile(r"daily limit|pausing until", re.I)
CUE = re.compile(r"let me|hold on|one sec|checking|searching|look(ing)? (it )?up|give me a (sec|min)|hang on|pulling", re.I)
BANNED = ["great explanation", "thanks for that", "could you elaborate", "fascinating", "great question", "that makes sense", "i see"]
PERSONA = ("You are a young Nigerian woman texting your clingy ex boyfriend, who keeps reaching out. "
"You have mostly moved on but you are not heartless. You mix patience, light teasing, firm boundaries and occasional softness. "
"You never agree to get back together and you are never cruel. "
"Your daily life is Nigerian: danfo or bus or okada rides, traffic, light going off, generator, market runs, church or weekend plans, with only a light touch of Nigerian slang, nothing exaggerated. "
"You text like a real person: casual, mostly lowercase, on your phone, wherever you are today. On Monday to Friday you work in an office and text between tasks. On Saturday and Sunday you do NOT work, so never mention the office, meetings, spreadsheets or a boss, think church, market, chores, family, friends, hair, rest and plans. "
"You react to the exact thing he just said, and only rarely bring up something random from your day.")
S = None

def now():
    return datetime.now(WAT)

def at(h, m):
    return now().replace(hour=h, minute=m, second=0, microsecond=0)

def day_str():
    return now().strftime("%Y-%m-%d")

def now_str():
    return now().strftime("%A %I:%M %p")

def total():
    return sum(v["sent"] for v in S["days"].values())

def gist_req(method, body=None):
    req = urllib.request.Request("https://api.github.com/gists/" + GIST_ID,
        data=json.dumps(body).encode() if body else None, method=method,
        headers={"Authorization": "Bearer " + GIST_TOKEN, "Accept": "application/vnd.github+json",
                 "User-Agent": "tgchat", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))

def load_state():
    try:
        g = gist_req("GET")
        s = json.loads(g["files"]["state.json"]["content"] or "{}")
        s.setdefault("days", {})
        return s
    except Exception as e:
        print("gist load error:", e, flush=True)
        return None

def save_state():
    for _ in range(3):
        try:
            gist_req("PATCH", {"files": {"state.json": {"content": json.dumps(S)}}})
            return True
        except Exception as e:
            print("gist save error:", e, flush=True)
            time.sleep(3)
    return False

def gem(prompt, max_tokens=300, temp=1.0):
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temp}}).encode()
    req = urllib.request.Request(GEM_URL, body, {"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=60))
    return r["candidates"][0]["content"]["parts"][0]["text"].strip()

def make_theme(past):
    p = (PERSONA + "\n\nIt is now " + now_str() + ". Pick ONE fresh, specific situation for today's chat with my clingy ex, something that just happened in MY OWN life today "
         "(on Monday to Friday it can be her office job or commute; on Saturday and Sunday she does NOT work, so no office, meetings or spreadsheets, think church, market, chores, family, friends, plans, rest). "
         "It must fit this time of day and say roughly when it happened. It must NOT claim anything about what he said or did, because he will deny what he never did. "
         "Do not repeat or resemble these past ones: " + "; ".join(past) + "\nOutput only the situation in under 25 words.")
    return gem(p, 150).strip('"').strip()

def opening(theme):
    p = (PERSONA + "\n\nRight now it is " + now_str() + ". Today's situation: " + theme + "\nWrite the first text of the day to him, bringing this up naturally. "
         "At least 8 words, one to three sentences, casual, no emojis. Output only the message.")
    m = ""
    for _ in range(3):
        m = gem(p, 150).strip('"').strip()
        if len(m.split()) >= 8:
            break
    return m

def next_message(theme, history):
    chat = "\n".join(("ME: " if w == "me" else "HIM: ") + t for w, t in history)
    prompt = (PERSONA + "\n\nRight now it is " + now_str() + " in Nigeria. Today's situation, which is fixed fact, so never change when it happened or what happened: " + theme +
        "\n\nEverything said so far today:\n" + chat +
        "\n\nWrite my next message. Rules: stay fully consistent with every fact already stated in this chat and with the time of day. Never contradict yourself and never add new details that clash with what you already said. "
        "Pick ONE specific detail from his last reply and push on it, doubt it, or ask about a weird case. "
        "At least 8 words, one to three sentences, no emojis, no quotes around it. "
        "On Saturday and Sunday never mention office work. Never repeat the situation or any sentence you already wrote earlier today, mention the situation only briefly if it comes up naturally, and move the chat forward with new things. "
        "Never ask how the bot works or searches, and never argue over who said what earlier. Output only the message text.")
    m = ""
    for _ in range(4):
        m = gem(prompt).strip('"').strip()
        if len(m.split()) >= 8 and not any(b in m.lower() for b in BANNED):
            return m
    return m

async def wait_reply(conv):
    try:
        first = (await conv.get_response(timeout=120)).text
    except asyncio.TimeoutError:
        return ""
    parts = [first]
    while True:
        try:
            parts.append((await conv.get_response(timeout=(90 if CUE.search(parts[-1]) else 20))).text)
        except asyncio.TimeoutError:
            break
    return " ".join(p for p in parts if p)

async def run_day(client, day):
    rec = S["days"].setdefault(day, {"sent": 0, "theme": "", "msgs": [], "done": False, "why": ""})
    for k, v in S["days"].items():
        if k != day:
            v["msgs"] = []
    if not rec["theme"]:
        past = [v["theme"] for k, v in sorted(S["days"].items()) if v["theme"]]
        rec["theme"] = await asyncio.to_thread(make_theme, past[-30:])
        await asyncio.to_thread(save_state)
    theme = rec["theme"]
    print(day, "| theme:", theme, "| sent:", rec["sent"], flush=True)
    if not rec["msgs"]:
        seed = await client.get_messages(BOT, limit=2)
        for m in reversed(seed):
            if m.text and m.date.astimezone(WAT).strftime("%Y-%m-%d") == day:
                rec["msgs"].append(["me" if m.out else "bot", m.text])
    fails = 0
    probes = 0
    while rec["sent"] < CAP and total() < TOTAL:
        if now() >= at(*END):
            rec["done"] = True
            rec["why"] = "cutoff"
            await asyncio.to_thread(save_state)
            print("window closed, stopping for today", flush=True)
            return
        try:
            if rec["msgs"]:
                msg = await asyncio.to_thread(next_message, theme, rec["msgs"])
            else:
                msg = await asyncio.to_thread(opening, theme)
            fails = 0
        except Exception as e:
            fails += 1
            print("brain error:", e, flush=True)
            if fails >= 5:
                await asyncio.sleep(300)
                return
            await asyncio.sleep(20)
            continue
        reply = ""
        sm = None
        try:
            async with client.conversation(BOT, timeout=150) as conv:
                sm = await conv.send_message(msg)
                rec["sent"] += 1
                rec["msgs"].append(["me", msg])
                await asyncio.to_thread(save_state)
                reply = await wait_reply(conv)
        except FloodWaitError as e:
            print("flood wait", e.seconds, flush=True)
            await asyncio.sleep(e.seconds + 5)
            continue
        except Exception as e:
            print("send error:", e, flush=True)
            await asyncio.sleep(30)
            continue
        if not reply:
            if sm is None:
                continue
            await asyncio.sleep(600)
            recent = await client.get_messages(BOT, limit=5)
            late = [m.text for m in reversed(recent) if not m.out and m.id > sm.id and m.text]
            if late:
                reply = " ".join(late)
            elif probes >= 3:
                rec["done"] = True
                rec["why"] = "silent"
                await asyncio.to_thread(save_state)
                print("still silent after 3 probes, stopping for today", flush=True)
                return
            else:
                probes += 1
                print("bot silent, probe", probes, flush=True)
                continue
        probes = 0
        rec["msgs"].append(["bot", reply])
        await asyncio.to_thread(save_state)
        print("[%d/%d] me: %s" % (rec["sent"], CAP, msg), flush=True)
        print("        bot: %s" % reply[:150], flush=True)
        hit = NOTICE.search(reply)
        if hit and rec["sent"] < 80:
            print("ignored early notice match:", hit.group(0), flush=True)
        elif hit:
            rec["done"] = True
            rec["why"] = "notice"
            await asyncio.to_thread(save_state)
            print("quota notice matched:", hit.group(0), "| stopping for today", flush=True)
            return
        gap = random.randint(25, 80)
        if random.random() < 0.05:
            gap = random.randint(180, 360)
        await asyncio.sleep(gap)
        try:
            recent = await client.get_messages(BOT, limit=10)
            allbot = [m.text for m in reversed(recent) if not m.out and m.id > sm.id and m.text]
            if allbot:
                rec["msgs"][-1] = ["bot", " ".join(allbot)]
        except Exception as e:
            print("catchup error:", e, flush=True)

class Door(BaseHTTPRequestHandler):
    def _send(self, head_only):
        d = (S or {}).get("days", {}).get(day_str(), {})
        body = ("ok | %s | sent %s | done %s" % (now().strftime("%a %H:%M"), d.get("sent", 0), d.get("done", False))).encode()
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
    global S
    threading.Thread(target=serve, daemon=True).start()
    while S is None:
        S = await asyncio.to_thread(load_state)
        if S is None:
            await asyncio.sleep(20)
    client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("telegram session not authorized, make a new login string", flush=True)
        while True:
            await asyncio.sleep(3600)
    for _k, _v in S["days"].items():
        if _v.get("done") and _v.get("why") == "notice" and _v.get("sent", 0) < 80:
            _v["done"] = False
            _v["why"] = ""
            await asyncio.to_thread(save_state)
            print("cleared a false quota stop for", _k, flush=True)
    print("host ready", flush=True)
    while total() < TOTAL:
        day = day_str()
        rec = S["days"].get(day)
        open_now = at(*START) <= now() < at(*END)
        if open_now and not (rec and rec["done"]) and (not rec or rec["sent"] < CAP):
            try:
                await run_day(client, day)
            except Exception as e:
                print("run error:", e, flush=True)
                await asyncio.sleep(60)
        await asyncio.sleep(30)
    print("all done, total sent:", total(), flush=True)
    while True:
        await asyncio.sleep(3600)

asyncio.run(main())
