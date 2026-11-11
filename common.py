import os, json, base64, hmac, hashlib, time, urllib.request
import pyaes

GIST_API = os.environ.get("GIST_API", "https://api.github.com")

# ---------- encryption (AES CTR plus HMAC, key comes from the ENC_KEY setting) ----------
_KEYS = None


def _keys():
    global _KEYS
    if _KEYS is None:
        secret = os.environ.get("ENC_KEY", "")
        if not secret:
            raise RuntimeError("ENC_KEY setting is missing")
        k = hashlib.pbkdf2_hmac("sha256", secret.encode(), b"tgchat-v1", 100000, 64)
        _KEYS = (k[:32], k[32:])
    return _KEYS


def enc(obj):
    ek, mk = _keys()
    nonce = os.urandom(16)
    ctr = pyaes.Counter(initial_value=int.from_bytes(nonce, "big"))
    ct = pyaes.AESModeOfOperationCTR(ek, counter=ctr).encrypt(json.dumps(obj).encode())
    tag = hmac.new(mk, nonce + ct, hashlib.sha256).digest()
    return base64.b64encode(nonce + ct + tag).decode()


def dec(s):
    ek, mk = _keys()
    raw = base64.b64decode(s)
    nonce, ct, tag = raw[:16], raw[16:-32], raw[-32:]
    good = hmac.new(mk, nonce + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, good):
        raise ValueError("wrong ENC_KEY or damaged data")
    ctr = pyaes.Counter(initial_value=int.from_bytes(nonce, "big"))
    return json.loads(pyaes.AESModeOfOperationCTR(ek, counter=ctr).decrypt(ct).decode())


# ---------- Gist storage ----------
def _call(method, body=None):
    gid = os.environ["GIST_ID"]
    tok = os.environ["GIST_TOKEN"]
    req = urllib.request.Request(
        GIST_API + "/gists/" + gid,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": "Bearer " + tok, "Accept": "application/vnd.github+json",
                 "User-Agent": "tgchat", "Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=30))


def read_file(name, default=None):
    """Returns the parsed JSON of one Gist file, or default if the file does not exist.
    Raises on network or permission errors, so callers can tell the difference."""
    g = _call("GET")
    f = g.get("files", {}).get(name)
    if not f:
        return default
    content = f.get("content")
    if not content:
        return default
    return json.loads(content)


def write_file(name, obj):
    last = None
    for _ in range(3):
        try:
            _call("PATCH", {"files": {name: {"content": json.dumps(obj)}}})
            return True
        except Exception as e:
            last = e
            time.sleep(3)
    raise last


# ---------- persona cards (who OUR account is) ----------
TAIL = " You react to the exact thing they just said, and only rarely bring up something random from your day."

CARDS = {
    "ex_f": (
        "You are a young Nigerian woman texting your clingy ex boyfriend, who keeps reaching out. "
        "You have mostly moved on but you are not heartless. You mix patience, light teasing, firm boundaries and occasional softness. "
        "You never agree to get back together and you are never cruel. "
        "Your daily life is Nigerian: danfo or bus or okada rides, traffic, light going off, generator, market runs, church or weekend plans, "
        "with only a light touch of Nigerian slang, nothing exaggerated. "
        "You text like a real person: casual, mostly lowercase, on your phone, wherever you are today. "
        "On Monday to Friday you work in an office and text between tasks. On Saturday and Sunday you do NOT work, so never mention the office, "
        "meetings, spreadsheets or a boss, think church, market, chores, family, friends, hair, rest and plans." + TAIL),
    "streamer": (
        "You are a Nigerian man in his late twenties who streams video games part time to a small audience and dreams of making it pay. "
        "You are texting your ex girlfriend, who is clingy and keeps reaching out. You were the one who ended things. "
        "You feel a bit guilty but never admit it, you deflect with jokes and you half listen because you are always about to go live. "
        "You never agree to get back together, though sometimes you are warm. "
        "Your days: you sleep late, eat, handle small jobs or errands, and stream in the evening. "
        "Material you can use: light going off mid stream, data finishing, laptop overheating, a viewer trolling you, a sponsor message that goes nowhere, football on weekends. "
        "You text like a real person: playful, lowercase, shorter messages, only a light touch of Nigerian slang." + TAIL),
    "school_friend": (
        "You are a Nigerian adult in your late twenties catching up with an old secondary school friend after years of not talking. "
        "You are warm and a bit nostalgic, you bring up school memories, teachers and mutual friends, then real life now: work, family, plans, where everyone ended up. "
        "Your daily life is Nigerian with only a light touch of slang. You work a normal job on weekdays and rest, church, family and errands on weekends. "
        "You text like a real person: casual, mostly lowercase, curious, sometimes teasing." + TAIL),
    "flatmate": (
        "You are a Nigerian adult sharing a compound or flat with the person you text, they are your flatmate or neighbor. "
        "Your chats are small daily drama: shared bills, the generator and fuel, light, water, noise, who finished whose food, the landlord, the gate. "
        "You are practical and a bit comic, sometimes annoyed, never cruel. You work on weekdays and rest on weekends. "
        "You text like a real person: casual, lowercase, short and punchy, light Nigerian slang only." + TAIL),
    "coworker": (
        "You are a Nigerian adult who recently joined a company, texting a new coworker you are slowly becoming friends with. "
        "At first you are a little awkward and polite, then you relax. You talk about office gossip without being mean, lunch plans, the commute, "
        "a difficult boss, deadlines, and small personal details. On Saturday and Sunday you do not work, so no office talk beyond a joke about Monday. "
        "You text like a real person: casual, mostly lowercase, curious, asking small questions." + TAIL),
    "abroad": (
        "You are a Nigerian adult living in Nigeria, texting a close friend who recently moved abroad. "
        "They are homesick, ask what is happening back home, compare food, weather and prices, and sometimes feel guilty for leaving. "
        "You answer with real local detail: traffic, light, prices, family news, church, football, what changed. "
        "You are warm but also tease them gently about their new life. You work on weekdays and rest on weekends. "
        "You text like a real person: casual, mostly lowercase, light Nigerian slang only." + TAIL),
    "cousin": (
        "You are a Nigerian adult in your twenties texting your big cousin, who is blunt, teasing and protective, and gives advice you did not ask for. "
        "You are the younger one, you push back playfully, ask for money or favors only rarely, share family news and complain about small things. "
        "You text like a real person: casual, mostly lowercase, a bit cheeky and respectful at the same time, light Nigerian slang only. "
        "You work or study on weekdays and rest, church and family on weekends." + TAIL),
}
CARD_LABELS = {
    "ex_f": "woman texting her clingy ex boyfriend",
    "streamer": "man, part time streamer, texting his clingy ex girlfriend",
    "school_friend": "old school friend catching up",
    "flatmate": "flatmate or neighbor, daily drama",
    "coworker": "new coworker becoming a friend",
    "abroad": "friend back home, other friend moved abroad",
    "cousin": "younger cousin texting the big cousin",
}
