from pathlib import Path
import json
import os
import sqlite3
import uuid
import re
import time
import unicodedata
import requests
import pytesseract
import io
import zipfile

from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from io import BytesIO
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote
from email.utils import formatdate
import base64
import hashlib

try:
    from rapidfuzz.fuzz import ratio as _fuzz_ratio
except Exception:
    from difflib import SequenceMatcher
    def _fuzz_ratio(a,b): return SequenceMatcher(None,a,b).ratio()*100


# ============================================================
# CIME DOS MUNDOS 5.0 BETA SMART VISION
# BIBLIOTECA INTELIGENTE + OCR + PROGRESSO + CACHE + QA
# ============================================================

BASE = os.path.dirname(os.path.abspath(__file__))

# Em Vercel, o código publicado é somente leitura. Dados mutáveis precisam ir para /tmp.
DATA_DIR = os.getenv("CIME_DATA_DIR") or os.path.join(BASE, "CimeDados")
os.makedirs(DATA_DIR, exist_ok=True)
DB = os.path.join(DATA_DIR, "cime.db")
MEDIA = os.path.join(DATA_DIR, "media")

PORT = int(os.getenv("CIME_PORT", "8790"))

TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

MAX_UPLOAD_SIZE = 15 * 1024 * 1024

JIKAN_URL = "https://api.jikan.moe/v4"
ANILIST_URL = "https://graphql.anilist.co"
JIKAN_TIMEOUT = 2
ANILIST_TIMEOUT = 3
CACHE_TTL_SECONDS = 30 * 24 * 60 * 60
KNOWN_ANIME_ALIASES = {
    "wistoria": {
        "canonical": "Tsue to Tsurugi no Wistoria",
        "english": "Wistoria: Wand and Sword",
        "aliases": ["Tsue to Tsurugi no Wistoria", "Wistoria: Wand and Sword", "Wistoria"]
    }
}


USER_AGENT = "CimeDosMundos/5.0 BETA SMART VISION PT"

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-5.6-sol").strip()
OPENAI_RESEARCH_MODEL = os.getenv("OPENAI_RESEARCH_MODEL", "gpt-5.6-sol").strip()
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
OPENAI_VISION_TIMEOUT = 8
OPENAI_VISION_DEEP_TIMEOUT = 12
OPENAI_RESEARCH_TIMEOUT = 12
OPENAI_VISION_MAX_IMAGE = 10 * 1024 * 1024
OPENAI_RESEARCH_CACHE_TTL = 7 * 24 * 60 * 60
TMDB_TOKEN = os.getenv("TMDB_ACCESS_TOKEN", "").strip()
KITSU_URL = "https://kitsu.io/api/edge"
KITSU_TIMEOUT = 3
SEARCH_MIN_CONFIDENCE = 62



# ============================================================
# IA VISUAL — OPENAI VISION
# ============================================================
VISION_SCHEMA = {
    "type": "object",
    "properties": {
        "recognized": {"type": "boolean"},
        "title": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string", "enum": ["Anime", "Série", "Filme", "Desenho", ""]},
        "confidence": {"type": "number"},
        "season": {"type": ["integer", "null"]},
        "season_title": {"type": "string"},
        "arc": {"type": "string"},
        "episode": {"type": ["integer", "null"]},
        "season_evidence": {"type": "string"},
        "evidence": {"type": "string"},
        "visible_title_text": {"type": "string"},
        "alternatives": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "confidence": {"type": "number"},
                "evidence": {"type": "string"}
            },
            "required": ["title", "confidence", "evidence"],
            "additionalProperties": False
        }}
    },
    "required": ["recognized", "title", "aliases", "category", "confidence", "season", "season_title", "arc", "episode", "season_evidence", "evidence", "visible_title_text", "alternatives"],
    "additionalProperties": False
}

RESEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "found": {"type": "boolean"},
        "canonical_title": {"type": "string"},
        "title_english": {"type": "string"},
        "title_japanese": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string", "enum": ["Anime", "Série", "Filme", "Desenho", ""]},
        "year": {"type": ["integer", "null"]},
        "status": {"type": "string"},
        "episodes": {"type": ["integer", "null"]},
        "score": {"type": ["number", "null"]},
        "genres": {"type": "array", "items": {"type": "string"}},
        "season": {"type": "string"},
        "season_number": {"type": ["integer", "null"]},
        "synopsis_pt": {"type": "string"},
        "cover_url": {"type": "string"},
        "release_status": {"type": "string"},
        "release_date": {"type": "string"},
        "next_season_number": {"type": ["integer", "null"]},
        "next_season_title": {"type": "string"},
        "confidence": {"type": "number"},
        "sources": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
            "required": ["title", "url"], "additionalProperties": False
        }}
    },
    "required": ["found", "canonical_title", "title_english", "title_japanese", "aliases", "category", "year", "status", "episodes", "score", "genres", "season", "season_number", "synopsis_pt", "cover_url", "release_status", "release_date", "next_season_number", "next_season_title", "confidence", "sources"],
    "additionalProperties": False
}

VISION_SYSTEM = """Você é a visão inteligente do Cime dos Mundos. Analise a imagem inteira como um humano, não apenas OCR.
OBJETIVO: identificar o anime, série, filme ou desenho principal mostrado na imagem.
REGRAS CRÍTICAS:
- NUNCA traduza o título para outra língua. Preserve o nome oficial/mais conhecido.
- Ignore botões, comentários, horários, curtidas, nomes de usuários, descrições, menus, legendas da interface e frases de conversa.
- Use o contexto visual: capa/poster, personagens, logotipo, layout, texto e idioma.
- Se o texto visível estiver quebrado, reconstrua usando o contexto visual.
- Se houver mais de uma obra, escolha a obra principal e descreva a evidência.
- Não transforme uma frase comum em título.
- Se o título estiver ilegível ou ausente, use personagens, logotipo, composição, cores, cenário e estilo como evidência visual; não dependa exclusivamente do OCR.
- Compare mentalmente as várias vistas da mesma imagem e não trate um crop como uma imagem diferente.
- Tente identificar explicitamente a TEMPORADA/SEASON. Procure pistas como \"Season 2\", \"2nd Season\", \"2ª temporada\", \"S2\", subtítulo do arco, ano e ordem da franquia.
- Se a temporada não estiver visível, deixe season nulo em vez de inventar.
- Se um arco corresponder inequivocamente a uma temporada conhecida, informe season, season_title e a evidência usada.
- Tente identificar episódio/capítulo quando houver marcação visível como EP 05, E05, Episode 5 ou equivalente.
- Na resposta season_evidence deve entrar o trecho visual que sustentou a temporada; se não houver, deixe vazio.
- Quando houver dúvida, forneça até 3 alternativas reais e distintas no campo alternatives.
- Nunca invente uma obra só porque ela parece visualmente parecida.
- Se não houver evidência suficiente, recognized=false e title vazio.
- confidence deve representar sua confiança real entre 0 e 1.
"""

def vision_configured():
    return bool(OPENAI_API_KEY)

def _prepare_vision_image(image_bytes):
    image = Image.open(BytesIO(image_bytes)).convert("RGB")
    # Mantém detalhes suficientes para texto pequeno sem mandar uma imagem gigantesca.
    image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
    out = BytesIO()
    image.save(out, format="JPEG", quality=90, optimize=True)
    data = out.getvalue()
    if len(data) > OPENAI_VISION_MAX_IMAGE:
        out = BytesIO()
        image.save(out, format="JPEG", quality=78, optimize=True)
        data = out.getvalue()
    return data

def _parse_json_from_text(text):
    text = str(text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, flags=re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: pass
    return {}

def _response_output_text(data):
    if isinstance(data, dict) and data.get("output_text"):
        return str(data.get("output_text"))
    parts=[]
    for item in (data.get("output",[]) if isinstance(data,dict) else []):
        for c in (item.get("content",[]) if isinstance(item,dict) else []):
            if isinstance(c,dict) and c.get("text"):
                parts.append(str(c.get("text")))
    return "\n".join(parts).strip()

def _openai_headers():
    return {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

def _valid_ai_title(title):
    title=str(title or "").strip()
    if not title: return False
    try:
        if _is_ui_phrase(title) or _looks_like_sentence(title): return False
    except Exception:
        pass
    if len(title)>120 or len(title.split())>12: return False
    return True

def _ocr_cache_key(image_bytes, mode="anchor"):
    return hashlib.sha256(("ocr:" + mode + ":").encode("utf-8") + image_bytes).hexdigest()

def _ocr_cache_get(image_bytes, mode="anchor"):
    key=_ocr_cache_key(image_bytes, mode)
    try:
        c=db(); row=c.execute("SELECT data,created_at FROM ocr_cache WHERE image_sha=? AND mode=?",(key,mode)).fetchone(); c.close()
        if row and time.time()-float(row["created_at"]) < CACHE_TTL_SECONDS:
            return json.loads(row["data"])
    except Exception:
        pass
    return None

def _ocr_cache_put(image_bytes, data, mode="anchor"):
    try:
        key=_ocr_cache_key(image_bytes, mode)
        c=db(); c.execute("INSERT OR REPLACE INTO ocr_cache(image_sha,mode,data,created_at) VALUES(?,?,?,?)",(key,mode,json.dumps(data,ensure_ascii=False),time.time())); c.commit(); c.close()
    except Exception:
        pass

def _vision_cache_key(image_bytes, mode="fast"):
    return hashlib.sha256((mode + ":").encode("utf-8") + image_bytes).hexdigest()

def _vision_cache_get(image_bytes, mode="fast"):
    key=_vision_cache_key(image_bytes, mode)
    try:
        c=db(); row=c.execute("SELECT data,created_at FROM ai_vision_cache WHERE image_sha=?",(key,)).fetchone(); c.close()
        if row and time.time()-float(row["created_at"]) < 30*24*60*60:
            data=json.loads(row["data"]); data["cached"]=True; data["vision_mode"]=mode; return data
    except Exception: pass
    return None

def _vision_cache_put(image_bytes,data, mode="fast"):
    try:
        key=_vision_cache_key(image_bytes, mode)
        c=db(); c.execute("INSERT OR REPLACE INTO ai_vision_cache(image_sha,data,created_at) VALUES(?,?,?)",(key,json.dumps(data,ensure_ascii=False),time.time())); c.commit(); c.close()
    except Exception: pass

def _prepare_vision_views(image_bytes, deep=False):
    """Fast = uma única vista inteira. Deep = inteira + regiões de título/arco."""
    image=Image.open(BytesIO(image_bytes)).convert("RGB")
    image.thumbnail((1280,1280) if not deep else (1600,1600), Image.Resampling.LANCZOS)
    w,h=image.size
    if not deep:
        boxes=[("full",(0,0,w,h))]
    else:
        boxes=[("full",(0,0,w,h)),("top",(0,0,w,max(1,int(h*0.38)))),("bottom",(0,int(h*0.58),w,h))]
    views=[]
    for name,box in boxes:
        crop=image.crop(box)
        crop.thumbnail((1350,1350) if not deep else (1450,1450), Image.Resampling.LANCZOS)
        out=BytesIO(); crop.save(out,format="JPEG",quality=84 if not deep else 80,optimize=True)
        views.append((name,out.getvalue()))
    return views

def openai_vision_analyze(image_bytes, ocr_hint="", deep=False):
    mode="deep" if deep else "fast"
    cached=_vision_cache_get(image_bytes, mode) if vision_configured() else None
    if cached is not None:
        return cached
    if not vision_configured():
        return {"configured":False,"recognized":False,"reason":"OPENAI_API_KEY não configurada.","vision_mode":mode}
    try:
        views=_prepare_vision_views(image_bytes, deep=deep)
        depth_note = "ANÁLISE PROFUNDA: use todas as vistas, confirme a franquia e temporada/arco." if deep else "CAMINHO RÁPIDO: use a vista inteira e responda sem procurar detalhes desnecessários."
        prompt=VISION_SYSTEM + "\n" + depth_note + "\nOCR local auxiliar (pode estar errado): " + str(ocr_hint or "")[:1800]
        content=[{"type":"input_text","text":prompt + "\nAs imagens abaixo são recortes da MESMA imagem original; não confunda texto promocional com o título da obra."}]
        for name,data in views:
            content.append({"type":"input_text","text":f"Vista: {name}"})
            content.append({"type":"input_image","image_url":f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}"})
        payload={
            "model": OPENAI_VISION_MODEL,
            "reasoning":{"effort":"low" if not deep else "medium"},
            "input":[{"role":"user","content":content}],
            "text":{"format":{"type":"json_schema","name":"cime_vision","strict":True,"schema":VISION_SCHEMA}}
        }
        timeout=OPENAI_VISION_DEEP_TIMEOUT if deep else OPENAI_VISION_TIMEOUT
        r=requests.post(OPENAI_RESPONSES_URL, headers=_openai_headers(), json=payload, timeout=timeout)
        if not r.ok:
            return {"configured":True,"recognized":False,"error":f"OpenAI Vision HTTP {r.status_code}","model":OPENAI_VISION_MODEL,"vision_mode":mode}
        data=r.json(); obj=_parse_json_from_text(_response_output_text(data))
        title=str(obj.get("title") or "").strip()
        try: conf=max(0,min(1,float(obj.get("confidence") or 0)))
        except Exception: conf=0.0
        valid_title=_valid_ai_title(title)
        recognized=bool(obj.get("recognized")) and valid_title and conf>=0.70
        aliases=[str(x).strip() for x in (obj.get("aliases") or []) if str(x).strip()][:8]
        alternatives=[]
        for a in (obj.get("alternatives") or [])[:4]:
            try:
                at=str(a.get("title") or "").strip(); ac=max(0.0,min(1.0,float(a.get("confidence") or 0)))
                if at and _valid_ai_title(at): alternatives.append({"title":at,"confidence":ac,"evidence":str(a.get("evidence") or "")})
            except Exception: continue
        season=obj.get("season")
        season_title=str(obj.get("season_title") or "")
        arc=str(obj.get("arc") or "")
        season_evidence=str(obj.get("season_evidence") or "")
        hint=infer_season_from_title_text(" ".join([title,season_title,arc,str(obj.get("visible_title_text") or "")]))
        if season is None and hint.get("verified_hint"): season=hint.get("season")
        if not season_title and hint.get("season_title"): season_title=hint.get("season_title")
        if not arc and hint.get("arc"): arc=hint.get("arc")
        result={"configured":True,"recognized":recognized,
                "title":title if recognized else "","title_raw":title if valid_title else "",
                "aliases":aliases,"category":str(obj.get("category") or ""),"confidence":conf,
                "season":season,"season_title":season_title,"arc":arc,"episode":obj.get("episode"),"season_evidence":season_evidence,
                "evidence":str(obj.get("evidence") or ""),"visible_title_text":str(obj.get("visible_title_text") or ""),
                "alternatives":alternatives,"model":OPENAI_VISION_MODEL,"cached":False,"vision_mode":mode}
        _vision_cache_put(image_bytes,result,mode)
        return result
    except Exception as e:
        return {"configured":True,"recognized":False,"error":str(e),"model":OPENAI_VISION_MODEL,"vision_mode":mode}


# ============================================================
# IA OPENAI — VISÃO + PESQUISA WEB EM UMA ÚNICA ETAPA
# ============================================================
COMBINED_SCHEMA = {
    "type": "object",
    "properties": {
        "recognized": {"type": "boolean"},
        "verified": {"type": "boolean"},
        "canonical_title": {"type": "string"},
        "title_english": {"type": "string"},
        "title_japanese": {"type": "string"},
        "aliases": {"type": "array", "items": {"type": "string"}},
        "category": {"type": "string", "enum": ["Anime", "Série", "Filme", "Desenho", ""]},
        "season": {"type": ["integer", "null"]},
        "episode": {"type": ["integer", "null"]},
        "year": {"type": ["integer", "null"]},
        "status": {"type": "string"},
        "episodes": {"type": ["integer", "null"]},
        "score": {"type": ["number", "null"]},
        "genres": {"type": "array", "items": {"type": "string"}},
        "synopsis_pt": {"type": "string"},
        "cover_url": {"type": "string"},
        "release_status": {"type": "string"},
        "release_date": {"type": "string"},
        "next_season_number": {"type": ["integer", "null"]},
        "next_season_title": {"type": "string"},
        "confidence": {"type": "number"},
        "evidence": {"type": "string"},
        "sources": {"type": "array", "items": {
            "type": "object",
            "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
            "required": ["title", "url"],
            "additionalProperties": False
        }}
    },
    "required": [
        "recognized", "verified", "canonical_title", "title_english", "title_japanese", "aliases",
        "category", "season", "episode", "year", "status", "episodes", "score", "genres",
        "synopsis_pt", "cover_url", "release_status", "release_date", "next_season_number",
        "next_season_title", "confidence", "evidence", "sources"
    ],
    "additionalProperties": False
}

COMBINED_SYSTEM = """Você é a IA principal do Cime dos Mundos. Você recebe UMA imagem e precisa fazer duas coisas: (1) entender visualmente qual obra está sendo mostrada e (2) confirmar essa obra pesquisando na web. Não use OCR como autoridade; OCR é apenas uma pista.

REGRAS DE IDENTIFICAÇÃO:
- Analise a imagem inteira como um humano.
- Procure o título principal na capa, logo, cabeçalho, poster ou página de streaming.
- Ignore botões, comentários, horários, curtidas, menus, nomes de usuários, descrições e textos secundários.
- NÃO traduza títulos. "Fire Force" continua "Fire Force". Um título traduzido só pode ser tratado como alias para procurar o título oficial.
- Se houver texto quebrado, reconstrua pelo contexto visual.
- Não aceite um trecho genérico como título (ex.: "Arte da", "Curtir", "Apareceu").
- Se a imagem não permitir uma identificação confiável, recognized=false.
- Se houver temporada/arco visível (por exemplo, "Swordsmith Village"), identifique temporada e arco separadamente.
- Nunca deduza uma temporada apenas por aparência do personagem; use texto visível e conhecimento verificável.

REGRAS DE PESQUISA:
- Depois de formular o título provável, pesquise na web para confirmar a obra.
- Não aceite uma coincidência apenas porque uma palavra é igual.
- Prefira fontes oficiais e bases fortes: site oficial, MyAnimeList/Jikan, AniList, Crunchyroll e bases reconhecidas; para séries/filmes use fontes apropriadas.
- Se houver várias temporadas, trate-as como uma única série e identifique a temporada correta.
- Para próxima temporada, informe a data oficial somente quando existir; nunca invente.
- Traduza somente a sinopse e rótulos para português do Brasil.
- Retorne somente o objeto JSON conforme o esquema.
"""

def openai_vision_web_identify(image_bytes, ocr_hint=""):
    if not vision_configured():
        return {"configured": False, "recognized": False, "found": False, "reason": "OPENAI_API_KEY não configurada."}
    try:
        prepared = _prepare_vision_image(image_bytes)
        b64 = base64.b64encode(prepared).decode("ascii")
        hint = str(ocr_hint or "").strip()[:1600]
        prompt = COMBINED_SYSTEM
        if hint:
            prompt += "\n\nOCR auxiliar (pode estar errado; nunca trate como autoridade):\n" + hint
        payload = {
            "model": OPENAI_VISION_MODEL,
            "reasoning": {"effort": "low"},
            "tools": [{"type": "web_search"}],
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": prompt},
                {"type": "input_image", "image_url": f"data:image/jpeg;base64,{b64}"}
            ]}],
            "text": {"format": {"type": "json_schema", "name": "cime_vision_web", "strict": True, "schema": COMBINED_SCHEMA}}
        }
        r = requests.post(OPENAI_RESPONSES_URL, headers=_openai_headers(), json=payload, timeout=OPENAI_VISION_TIMEOUT)
        if not r.ok:
            return {"configured": True, "recognized": False, "found": False, "error": f"OpenAI Vision+Web HTTP {r.status_code}", "model": OPENAI_VISION_MODEL}
        data = r.json()
        obj = _parse_json_from_text(_response_output_text(data))
        title = str(obj.get("canonical_title") or "").strip()
        try: conf = max(0.0, min(1.0, float(obj.get("confidence") or 0)))
        except Exception: conf = 0.0
        recognized = bool(obj.get("recognized")) and _valid_ai_title(title) and conf >= 0.72
        found = bool(obj.get("verified")) and recognized and bool(title)
        return {
            "configured": True,
            "recognized": recognized,
            "found": found,
            "title": title if recognized else "",
            "canonical_title": title if found else (title if recognized else ""),
            "title_english": str(obj.get("title_english") or "").strip(),
            "title_japanese": str(obj.get("title_japanese") or "").strip(),
            "aliases": [str(x).strip() for x in (obj.get("aliases") or []) if str(x).strip()][:8],
            "category": str(obj.get("category") or ""),
            "season": obj.get("season"),
            "season_number": obj.get("season"),
            "episode": obj.get("episode"),
            "year": obj.get("year"),
            "status": str(obj.get("status") or ""),
            "episodes": obj.get("episodes"),
            "score": obj.get("score"),
            "genres": [str(x).strip() for x in (obj.get("genres") or []) if str(x).strip()][:12],
            "synopsis_pt": str(obj.get("synopsis_pt") or ""),
            "cover_url": str(obj.get("cover_url") or ""),
            "release_status": str(obj.get("release_status") or ""),
            "release_date": str(obj.get("release_date") or ""),
            "next_season_number": obj.get("next_season_number"),
            "next_season_title": str(obj.get("next_season_title") or ""),
            "confidence": conf,
            "evidence": str(obj.get("evidence") or ""),
            "sources": obj.get("sources") if isinstance(obj.get("sources"), list) else [],
            "model": OPENAI_VISION_MODEL,
            "combined": True,
        }
    except Exception as e:
        return {"configured": True, "recognized": False, "found": False, "error": str(e), "model": OPENAI_VISION_MODEL}

def _cache_ai_get(query):
    key=normalizar_texto(query)
    if not key: return None
    try:
        conn=db(); row=conn.execute("SELECT data,created_at FROM ai_research_cache WHERE query=?",(key,)).fetchone(); conn.close()
        if row and time.time()-float(row["created_at"]) < OPENAI_RESEARCH_CACHE_TTL:
            return json.loads(row["data"])
    except Exception: pass
    return None

def _cache_ai_put(query, data):
    key=normalizar_texto(query)
    if not key: return
    try:
        conn=db(); conn.execute("INSERT OR REPLACE INTO ai_research_cache(query,data,created_at) VALUES(?,?,?)",(key,json.dumps(data,ensure_ascii=False),time.time())); conn.commit(); conn.close()
    except Exception: pass

def openai_web_research(title, aliases=None, category_hint=""):
    if not vision_configured():
        return {"configured":False,"found":False,"reason":"OPENAI_API_KEY não configurada."}
    title=str(title or "").strip()
    if not title: return {"configured":True,"found":False,"reason":"Título vazio."}
    cached=_cache_ai_get(title)
    if cached is not None:
        cached["cached"]=True; return cached
    aliases=[str(x).strip() for x in (aliases or []) if str(x).strip()][:5]
    alias_text=", ".join(aliases)
    prompt=f"""Você é o pesquisador do Cime dos Mundos. Pesquise na web e identifique com alta precisão esta obra: {title!r}.
Possíveis títulos alternativos: {alias_text or 'nenhum'}.
Tipo sugerido: {category_hint or 'desconhecido'}.
REGRAS:
1) O título retornado deve ser o nome oficial/mais conhecido, nunca uma tradução inventada.
2) Confirme que a obra realmente existe e que corresponde ao título pesquisado; não aceite resultados apenas porque compartilham uma palavra genérica.
3) Priorize fontes oficiais (site do estúdio/distribuidora/emissora), bases conhecidas e páginas recentes; use múltiplas fontes quando houver dúvida.
4) Para anime, priorize MyAnimeList/Jikan, AniList e fonte oficial; para séries, use fonte oficial/TVMaze/IMDb quando relevante; para filmes, fontes oficiais/IMDb/Wikidata quando úteis.
5) Se for uma próxima temporada, procure anúncio oficial e data de estreia. Se a data não existir, diga explicitamente que ainda não foi anunciada; nunca invente.
6) Traduza apenas a sinopse para português do Brasil e os rótulos de gêneros/status para a interface.
7) Retorne JSON seguindo exatamente o esquema solicitado. Se a evidência não for suficiente, found=false.
"""
    payload={
        "model": OPENAI_RESEARCH_MODEL,
        "tools":[{"type":"web_search"}],
        "input":prompt,
        "text":{"format":{"type":"json_schema","name":"cime_research","strict":True,"schema":RESEARCH_SCHEMA}}
    }
    try:
        r=requests.post(OPENAI_RESPONSES_URL,headers=_openai_headers(),json=payload,timeout=OPENAI_RESEARCH_TIMEOUT)
        if not r.ok:
            return {"configured":True,"found":False,"error":f"OpenAI Web Search HTTP {r.status_code}","model":OPENAI_RESEARCH_MODEL}
        data=r.json(); obj=_parse_json_from_text(_response_output_text(data))
        result={"configured":True,"found":bool(obj.get("found")),"canonical_title":str(obj.get("canonical_title") or "").strip(),"title":str(obj.get("canonical_title") or "").strip(),"title_english":str(obj.get("title_english") or "").strip(),"title_japanese":str(obj.get("title_japanese") or "").strip(),"aliases":[str(x).strip() for x in (obj.get("aliases") or []) if str(x).strip()][:8],"category":str(obj.get("category") or ""),"year":obj.get("year"),"status":str(obj.get("status") or ""),"episodes":obj.get("episodes"),"score":obj.get("score"),"genres":[str(x).strip() for x in (obj.get("genres") or []) if str(x).strip()],"season":str(obj.get("season") or ""),"season_number":obj.get("season_number"),"synopsis_pt":str(obj.get("synopsis_pt") or ""),"cover_url":str(obj.get("cover_url") or ""),"release_status":str(obj.get("release_status") or ""),"release_date":str(obj.get("release_date") or ""),"next_season_number":obj.get("next_season_number"),"next_season_title":str(obj.get("next_season_title") or ""),"confidence":obj.get("confidence"),"sources":obj.get("sources") if isinstance(obj.get("sources"),list) else [],"model":OPENAI_RESEARCH_MODEL,"cached":False}
        if result["found"] and result["canonical_title"]:
            _cache_ai_put(title,result)
        return result
    except Exception as e:
        return {"configured":True,"found":False,"error":str(e),"model":OPENAI_RESEARCH_MODEL}

# ============================================================
# CONFIGURAÇÃO DO TESSERACT
# ============================================================

def _find_tesseract():
    """Encontra o Tesseract de forma portável (Windows/Linux/macOS)."""
    candidates = [
        os.getenv("TESSERACT_CMD", "").strip(),
        TESSERACT,
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        "/usr/bin/tesseract",
        "/usr/local/bin/tesseract",
        "/opt/homebrew/bin/tesseract",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    try:
        import shutil
        found = shutil.which("tesseract")
        if found:
            return found
    except Exception:
        pass
    return ""

TESSERACT = _find_tesseract() or TESSERACT
pytesseract.pytesseract.tesseract_cmd = TESSERACT

def _ocr_language():
    """Escolhe o melhor idioma instalado sem quebrar em máquinas sem por.traineddata."""
    try:
        langs = set(pytesseract.get_languages(config=""))
    except Exception:
        langs = set()
    if "por" in langs and "eng" in langs:
        return "por+eng"
    if "eng" in langs:
        return "eng"
    if "por" in langs:
        return "por"
    return "eng"

OCR_LANGUAGE = _ocr_language()


# ============================================================
# PASTAS
# ============================================================

os.makedirs(MEDIA, exist_ok=True)


# ============================================================
# BANCO DE DADOS
# ============================================================

def db():
    conn = sqlite3.connect(
        DB,
        timeout=10
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS titles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'Anime',
            status TEXT NOT NULL DEFAULT 'Quero assistir',
            rating REAL,
            start_date TEXT,
            end_date TEXT,
            cover TEXT,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)

    conn.execute("CREATE TABLE IF NOT EXISTS anime_cache (query TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS ai_research_cache (query TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS ai_vision_cache (image_sha TEXT PRIMARY KEY, data TEXT NOT NULL, created_at REAL NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS ocr_cache (image_sha TEXT PRIMARY KEY, mode TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_ocr_cache_created ON ocr_cache(created_at)")
    conn.execute("""CREATE TABLE IF NOT EXISTS watch_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title_id INTEGER NOT NULL,
        season INTEGER NOT NULL DEFAULT 1,
        episode INTEGER NOT NULL DEFAULT 0,
        watched_at TEXT NOT NULL,
        FOREIGN KEY(title_id) REFERENCES titles(id) ON DELETE CASCADE
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_history_title ON watch_history(title_id, watched_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_history_watched_at ON watch_history(watched_at DESC)")
    conn.execute("""CREATE TABLE IF NOT EXISTS imported_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, source_title TEXT NOT NULL, watched_at TEXT DEFAULT '', profile TEXT DEFAULT '', season INTEGER, episode INTEGER, title_id INTEGER, created_at TEXT NOT NULL,
        UNIQUE(source,source_title,watched_at,profile)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imported_history_source ON imported_history(source, created_at DESC)")
    try:
        conn.execute("DELETE FROM watch_history WHERE id NOT IN (SELECT MIN(id) FROM watch_history GROUP BY title_id, season, episode)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_history_episode ON watch_history(title_id, season, episode)")
    except sqlite3.IntegrityError:
        pass

    # Migração segura: versões anteriores não tinham o total de episódios.
    cols = {row[1] for row in conn.execute("PRAGMA table_info(titles)").fetchall()}
    if "episodes" not in cols:
        conn.execute("ALTER TABLE titles ADD COLUMN episodes INTEGER")

    migrations = {
        "current_episode": "INTEGER NOT NULL DEFAULT 0",
        "current_season": "INTEGER NOT NULL DEFAULT 1",
        "total_seasons": "INTEGER",
        "favorite": "INTEGER NOT NULL DEFAULT 0",
        "last_watched_at": "TEXT DEFAULT ''",
        "title_english": "TEXT DEFAULT ''",
        "title_japanese": "TEXT DEFAULT ''",
        "synopsis": "TEXT DEFAULT ''",
        "year": "INTEGER",
        "season": "TEXT DEFAULT ''",
        "duration": "TEXT DEFAULT ''",
        "genres": "TEXT DEFAULT ''",
        "source_id": "TEXT DEFAULT ''",
        "watch_rate": "REAL DEFAULT 2",
        "completed_at": "TEXT DEFAULT ''",
        "franchise_key": "TEXT DEFAULT ''",
        "season_covers": "TEXT DEFAULT '[]'",
        "seasons_json": "TEXT DEFAULT '[]'",
        "total_episodes": "INTEGER",
        "synopsis_pt": "TEXT DEFAULT ''",
        "local_cover_photos": "TEXT DEFAULT '[]'",
        "cover_collage": "TEXT DEFAULT ''",
        "history_sources": "TEXT DEFAULT '[]'"
    }
    for col, definition in migrations.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE titles ADD COLUMN {col} {definition}")

    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_status ON titles(status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_favorite ON titles(favorite)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_source_id ON titles(source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_last_watched ON titles(last_watched_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_category ON titles(category)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_created ON titles(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_titles_franchise ON titles(franchise_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_imported_history_title ON imported_history(title_id, created_at DESC)")

    conn.commit()
    conn.close()
    try:
        migrate_franchise_groups()
        repair_all_progress_positions()
    except Exception as e:
        print("Migração/reparo de temporadas ignorado:", e)


# ============================================================
# BIBLIOTECA
# ============================================================

_LIBRARY_CACHE = {}
_LIBRARY_CACHE_LIMIT = 4

def _library_db_stamp():
    try:
        conn=db(); row=conn.execute('PRAGMA database_list').fetchone(); path=str(row[2] or '') if row else str(DB); conn.close()
    except Exception:
        path=str(DB)
    return (path,)

def invalidate_library_cache():
    _LIBRARY_CACHE.clear()

def _library_cached(key, builder):
    hit = _LIBRARY_CACHE.get(key)
    if hit is not None:
        return hit
    value = builder()
    _LIBRARY_CACHE[key] = value
    while len(_LIBRARY_CACHE) > _LIBRARY_CACHE_LIMIT:
        _LIBRARY_CACHE.pop(next(iter(_LIBRARY_CACHE)))
    return value

def all_titles():
    stamp = _library_db_stamp()
    key = ('titles', stamp)
    return _library_cached(key, lambda: _load_all_titles())

def _load_all_titles():
    conn = db()
    rows = conn.execute("""
        SELECT *
        FROM titles
        ORDER BY favorite DESC, id DESC
    """).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_title(title_id):

    conn = db()

    row = conn.execute(
        """
        SELECT *
        FROM titles
        WHERE id = ?
        """,
        (title_id,)
    ).fetchone()

    conn.close()

    if not row:
        return None

    return dict(row)


def hoje_br():
    return datetime.now().strftime("%d/%m/%Y")


def _int_or_none(value, default=None, minimum=0):
    try:
        if value in (None, ""):
            return default
        value = int(value)
        return value if value >= minimum else default
    except Exception:
        return default


def _norm_name(value):
    return normalizar_texto(str(value or ""))


def find_existing_title(name, source_id=""):
    conn = db()
    row = None
    if source_id:
        row = conn.execute("SELECT * FROM titles WHERE source_id=? ORDER BY id DESC LIMIT 1", (str(source_id),)).fetchone()
    if not row:
        n = _norm_name(name)
        for candidate in conn.execute("SELECT * FROM titles ORDER BY id DESC").fetchall():
            if _norm_name(candidate["name"]) == n or (candidate["title_english"] and _norm_name(candidate["title_english"]) == n):
                row = candidate
                break
    conn.close()
    return dict(row) if row else None


def _auto_datas(status, start_date, end_date):
    status = str(status or "").strip()
    start_date = str(start_date or "").strip()
    end_date = str(end_date or "").strip()
    if status == "Assistindo" and not start_date:
        start_date = hoje_br()
    if status == "Concluído" and not end_date:
        end_date = hoje_br()
        if not start_date:
            start_date = hoje_br()
    return start_date, end_date



def season_number_from_title(name):
    t = normalizar_texto(str(name or ""))
    for pattern in (r"\bseason\s*(\d+)\b", r"\btemporada\s*(\d+)\b", r"\bs\s*(\d+)\b", r"\b(\d+)(?:st|nd|rd|th)\s+season\b"):
        m = re.search(pattern, t, re.I)
        if m:
            try: return max(1, int(m.group(1)))
            except Exception: pass
    return 1

def franchise_key_from_title(name):
    t = normalizar_texto(str(name or ""))
    t = re.sub(r"\bseason\s*\d+\b|\btemporada\s*\d+\b|\bs\s*\d+\b|\b\d+(?:st|nd|rd|th)\s+season\b", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if "wistoria" in t or "tsue to tsurugi no wistoria" in t:
        return "tsue to tsurugi no wistoria"
    return t

def _json_load(value, default):
    try: return json.loads(value) if value else default
    except Exception: return default

def _season_entry_from_row(row):
    d=dict(row)
    return {"season":season_number_from_title(d.get("name") or d.get("title_english") or ""), "name":d.get("name") or "", "title_english":d.get("title_english") or "", "episodes":d.get("episodes"), "score":d.get("rating"), "status":d.get("status") or "", "year":d.get("year"), "season_name":d.get("season") or "", "cover":d.get("cover") or "", "source_id":d.get("source_id") or "", "synopsis":d.get("synopsis") or "", "synopsis_pt":d.get("synopsis_pt") or ""}

def _merge_season_entries(entries):
    merged={}
    for item in entries:
        try:n=max(1,int(item.get("season") or 1))
        except Exception:n=1
        if n not in merged: merged[n]=dict(item)
        else:
            for k,v in item.items():
                if v not in (None,"",[],{}): merged[n][k]=v
    return [merged[k] for k in sorted(merged)]


def _season_entries(row):
    raw=_json_load(row.get("seasons_json"),[]) if row else []
    if not raw and row:
        raw=[_season_entry_from_row(row)]
    return _merge_season_entries(raw)

def _season_info(row, season_number):
    try: wanted=max(1,int(season_number or 1))
    except Exception: wanted=1
    entries=_season_entries(row)
    for e in entries:
        try:
            if int(e.get("season") or 1)==wanted:
                return e
        except Exception:
            continue
    return None

def _next_known_season(row, season_number):
    entries=_season_entries(row)
    for e in entries:
        try:
            n=int(e.get("season") or 1)
        except Exception:
            continue
        if n>int(season_number or 1) and _int_or_none(e.get("episodes"),None):
            return e
    return None

def _normalize_progress_position(row, season_number, episode):
    season=max(1,int(season_number or 1)); ep=max(0,int(episode or 0)); status=row.get("status") or "Quero assistir"
    # When a season is complete, move automatically to the next season that has a known episode count.
    for _ in range(10):
        info=_season_info(row, season)
        season_eps=_int_or_none((info or {}).get("episodes"), None)
        if not season_eps or ep < season_eps:
            break
        next_info=_season_info(row, season+1)
        next_eps=_int_or_none((next_info or {}).get("episodes"), None)
        if next_info and next_eps:
            season += 1
            ep = 0
            status = "Assistindo"
            continue
        if next_info and not next_eps:
            ep = season_eps
            status = "Aguardando"
        else:
            ep = season_eps
            status = "Concluído"
        break
    return season, ep, status

def _sanitize_seasons_from_payload(row, payload):
    current=_season_entries(row)
    if payload in (None, ""):
        return current
    try:
        incoming=json.loads(payload) if isinstance(payload,str) else payload
    except Exception:
        incoming=current
    if not isinstance(incoming,list):
        return current
    by={int(e.get("season") or 1):dict(e) for e in current if isinstance(e,dict)}
    for item in incoming:
        if not isinstance(item,dict): continue
        try:n=max(1,int(item.get("season") or 1))
        except Exception:continue
        base=dict(by.get(n,{}))
        base.update({k:v for k,v in item.items() if v not in (None,)})
        base["season"]=n
        base["episodes"]=_int_or_none(base.get("episodes"), None)
        try: base["score"]=float(base.get("score")) if base.get("score") not in (None,"") else None
        except Exception: base["score"]=None
        by[n]=base
    return [by[k] for k in sorted(by)]

def _franchise_payload(row=None, new_data=None):
    entries=[]
    if row:
        existing=_json_load(row.get("seasons_json"),[])
        entries.extend(existing or [_season_entry_from_row(row)])
    if new_data is not None:
        rating=new_data.get("rating")
        try: rating=float(rating) if rating not in (None,"") else None
        except Exception: rating=None
        entries.append({"season":season_number_from_title(new_data.get("name","")), "name":str(new_data.get("name", "")), "title_english":str(new_data.get("title_english","") or ""), "episodes":_int_or_none(new_data.get("episodes"),None), "score":rating, "status":str(new_data.get("status","")), "year":_int_or_none(new_data.get("year"),None), "season_name":str(new_data.get("season","") or ""), "cover":str(new_data.get("cover","") or ""), "source_id":str(new_data.get("source_id","") or ""), "synopsis":str(new_data.get("synopsis","") or ""), "synopsis_pt":str(new_data.get("synopsis_pt","") or "")})
    entries=_merge_season_entries(entries)
    covers=[e.get("cover") for e in entries if e.get("cover")]
    return entries,covers

def repair_all_progress_positions():
    """Converte progresso antigo baseado no total da série para temporada/episódio."""
    conn = db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM titles").fetchall()]
    for row in rows:
        seasons = _season_entries(row)
        if not seasons:
            continue
        current_season = max(1, int(row.get("current_season") or 1))
        current_episode = max(0, int(row.get("current_episode") or 0))
        # Se o valor antigo ultrapassar o total da temporada atual, trate como progresso global.
        current_info = _season_info(row, current_season)
        current_eps = _int_or_none((current_info or {}).get("episodes"), None)
        if current_eps and current_episode > current_eps:
            remaining = current_episode
            chosen = current_season
            for e in seasons:
                n = int(e.get("season") or 1)
                eps = _int_or_none(e.get("episodes"), None)
                if not eps:
                    continue
                if n < current_season:
                    continue
                if remaining > eps:
                    remaining -= eps
                    chosen = n + 1
                else:
                    chosen = n
                    break
            if chosen > current_season:
                current_season = chosen
                current_episode = max(0, remaining if chosen == current_season else 0)
        tmp = dict(row); tmp["seasons_json"] = json.dumps(seasons, ensure_ascii=False)
        current_season, current_episode, status = _normalize_progress_position(tmp, current_season, current_episode)
        info = _season_info(tmp, current_season)
        season_eps = _int_or_none((info or {}).get("episodes"), None)
        if season_eps is not None:
            conn.execute("UPDATE titles SET current_season=?, current_episode=?, episodes=? WHERE id=?", (current_season, min(current_episode, season_eps), season_eps, row["id"]))
        else:
            conn.execute("UPDATE titles SET current_season=?, current_episode=? WHERE id=?", (current_season, current_episode, row["id"]))
        if status in ("Assistindo", "Aguardando", "Concluído"):
            conn.execute("UPDATE titles SET status=? WHERE id=?", (status, row["id"]))
    conn.commit(); conn.close()

def migrate_franchise_groups():
    conn=db(); rows=[dict(r) for r in conn.execute("SELECT * FROM titles ORDER BY id ASC").fetchall()]; groups={}
    for row in rows: groups.setdefault(row.get("franchise_key") or franchise_key_from_title(row.get("name","")) or f"title:{row['id']}",[]).append(row)
    for key,items in groups.items():
        all_entries=[]
        for item in items: all_entries.extend(_json_load(item.get("seasons_json"),[]) or [_season_entry_from_row(item)])
        entries=_merge_season_entries(all_entries); covers=[e.get("cover") for e in entries if e.get("cover")]
        base=next((i for i in items if season_number_from_title(i.get("name",""))==1 and "season" not in normalizar_texto(i.get("name",""))),items[0])
        total_eps=sum(int(e.get("episodes") or 0) for e in entries) or (base.get("episodes") or None)
        first_eps=next((int(e.get("episodes")) for e in entries if int(e.get("season") or 1)==1 and e.get("episodes") is not None), base.get("episodes"))
        conn.execute("""UPDATE titles SET name=?, title_english=?, cover=?, franchise_key=?, season_covers=?, seasons_json=?, total_seasons=?, episodes=?, total_episodes=?, rating=?, status=?, source_id=?, synopsis=?, synopsis_pt=? WHERE id=?""", (base.get("name") or items[0].get("name"), base.get("title_english") or "", covers[0] if covers else base.get("cover") or "", key, json.dumps(covers,ensure_ascii=False), json.dumps(entries,ensure_ascii=False), len(entries), first_eps, total_eps, base.get("rating"), base.get("status"), base.get("source_id") or "", base.get("synopsis") or "", base.get("synopsis_pt") or "", items[0]["id"]))
        for item in items[1:]: conn.execute("DELETE FROM titles WHERE id=?",(item["id"],))
    conn.commit(); conn.close()

def find_franchise_title(name):
    key=franchise_key_from_title(name); conn=db(); row=conn.execute("SELECT * FROM titles WHERE franchise_key=? ORDER BY id ASC LIMIT 1",(key,)).fetchone(); conn.close(); return dict(row) if row else None

def group_existing_title_data(title_id,new_data):
    row=get_title(title_id)
    if not row:return None
    entries,covers=_franchise_payload(row=row,new_data=new_data)
    local_old=_safe_local_photo_paths(row.get("local_cover_photos")) if "_safe_local_photo_paths" in globals() else []
    local_new=_safe_local_photo_paths(new_data.get("local_cover_photos")) if "_safe_local_photo_paths" in globals() else []
    local_all=local_old+local_new
    local_collage=build_cover_collage(local_all) if local_all else ""
    base=next((e for e in entries if int(e.get("season") or 1)==1),entries[0])
    total_eps=sum(int(e.get("episodes") or 0) for e in entries) or row.get("episodes") or None
    first_eps=next((int(e.get("episodes")) for e in entries if int(e.get("season") or 1)==1 and e.get("episodes") is not None), _int_or_none(base.get("episodes"), row.get("episodes")))
    conn=db(); conn.execute("""UPDATE titles SET name=?, title_english=?, cover=?, franchise_key=?, season_covers=?, seasons_json=?, total_seasons=?, episodes=?, total_episodes=?, synopsis=?, synopsis_pt=?, local_cover_photos=?, cover_collage=? WHERE id=?""", (base.get("name") or row["name"], base.get("title_english") or row.get("title_english") or "", local_collage or (covers[0] if covers else row.get("cover") or ""), franchise_key_from_title(base.get("name") or row["name"]), json.dumps(covers,ensure_ascii=False), json.dumps(entries,ensure_ascii=False), len(entries), first_eps, total_eps, base.get("synopsis") or row.get("synopsis") or "", base.get("synopsis_pt") or row.get("synopsis_pt") or "", json.dumps(local_all,ensure_ascii=False), local_collage, title_id)); conn.commit(); conn.close(); return get_title(title_id)


def _safe_local_photo_paths(value):
    if value in (None, '', []): return []
    try: raw=json.loads(value) if isinstance(value,str) else value
    except Exception: raw=[]
    if not isinstance(raw,list): raw=[raw]
    out=[]; seen=set()
    for item in raw:
        if isinstance(item,str): path=item; season=None
        elif isinstance(item,dict): path=str(item.get('path') or '').strip(); season=item.get('season')
        else: continue
        if not path.startswith('/media/'): continue
        name=path[len('/media/'):]
        if not name or '/' in name or '\\' in name: continue
        try: season=int(season) if season not in (None,'') else None
        except Exception: season=None
        k=(name,season)
        if k not in seen:
            seen.add(k); out.append({'path':'/media/'+name,'season':season})
    return out

def _media_path(public_path):
    if not str(public_path or '').startswith('/media/'): return None
    name=str(public_path)[7:]
    if not name or '/' in name or '\\' in name: return None
    p=Path(os.fspath(MEDIA))/name
    return p if p.exists() and p.is_file() else None

def build_cover_collage(items):
    items=_safe_local_photo_paths(items)
    loaded=[]
    for item in items[:8]:
        p=_media_path(item['path'])
        if not p: continue
        try:
            im=Image.open(p).convert('RGB')
            loaded.append((item,im.copy()))
        except Exception: pass
    if not loaded: return ''
    maxw=900; gap=6; thumbs=[]; total=0; width=0
    for item,im in loaded:
        im.thumbnail((maxw,maxw),Image.Resampling.LANCZOS)
        width=max(width,im.width); total+=im.height
        thumbs.append((item,im))
    total+=gap*max(0,len(thumbs)-1)
    canvas=Image.new('RGB',(width,total),(7,10,17)); y=0
    for _,im in thumbs:
        canvas.paste(im,((width-im.width)//2,y)); y+=im.height+gap
    name='cover_stack_'+uuid.uuid4().hex+'.jpg'; out=Path(os.fspath(MEDIA))/name
    canvas.save(out,'JPEG',quality=90,optimize=True)
    return '/media/'+name

def attach_local_cover_photos(title_id, items):
    row=get_title(title_id)
    if not row: return None
    merged=_safe_local_photo_paths(row.get('local_cover_photos'))+_safe_local_photo_paths(items)
    clean=[]; seen=set()
    for x in merged:
        k=(x['path'],x.get('season'))
        if k not in seen: seen.add(k); clean.append(x)
    collage=build_cover_collage(clean)
    conn=db(); conn.execute('UPDATE titles SET local_cover_photos=?, cover_collage=?, cover=? WHERE id=?',(json.dumps(clean,ensure_ascii=False),collage,collage or row.get('cover',''),title_id)); conn.commit(); conn.close()
    return get_title(title_id)

def _parse_import_series_title(title):
    """Retorna (titulo_base, temporada_detectada)."""
    raw=str(title or '').strip()
    if not raw:
        return '', None
    season_patterns=[
        r'\s*(?:[:\-]\s*)?(?:temporada|season|s)\s*(\d+)\s*(?:[:\-]?\s*)?(?:episode|episódio|ep\.?|e)\s*\d+\b.*$',
        r'\s*[:\-]\s*(?:temporada|season)\s*(\d+)\b.*$',
        r'\s*[:\-]\s*(?:t|s)\s*(\d+)\s*(?:[:\-].*)?$',
    ]
    for pat in season_patterns:
        m=re.search(pat, raw, flags=re.I)
        if m:
            try:n=max(1,int(m.group(1)))
            except Exception:n=1
            base=raw[:m.start()].strip(' :-')
            if base:
                return base, n
    ep_patterns=[
        r'\s*(?:[:\-]\s*)?(?:episode|episódio|ep\.?)[\s\-:]*\d+.*$',
        r'\s*(?:[:\-]\s*)?s\d+\s*e\d+.*$',
    ]
    for pat in ep_patterns:
        cleaned=re.sub(pat,'',raw,flags=re.I).strip(' :-')
        if cleaned and cleaned.lower()!=raw.lower():
            return cleaned, None
    return raw, None

def _canonical_import_title(title):
    return _parse_import_series_title(title)[0]

def _infer_import_category(title, rows):
    if any(r.get('season') or r.get('episode') for r in rows):
        return 'Série'
    if any(re.search(r'\b(?:episode|episódio|ep\.?|s\d+e\d+|temporada|season)\b', str(r.get('title','')), re.I) for r in rows):
        return 'Série'
    return 'Filme/Série'

def _find_import_title_id(conn, title):
    target=normalizar_texto(title); best=None; best_score=0
    rows=conn.execute('SELECT id,name,title_english,title_japanese FROM titles').fetchall()
    for r in rows:
        for nm in (r['name'] or '', r['title_english'] or '', r['title_japanese'] or ''):
            rn=normalizar_texto(nm)
            if not rn: continue
            score=100.0 if rn==target else (92.0 if rn in target or target in rn else float(_fuzz_ratio(rn,target)))
            if score>best_score:
                best_score=score; best=r['id']
    return best if best_score>=82 else None

def consolidate_imported_seasons():
    """Junta títulos importados que tenham marcador explícito de temporada."""
    conn=db()
    rows=[dict(r) for r in conn.execute("SELECT DISTINCT t.* FROM titles t JOIN imported_history h ON h.title_id=t.id WHERE lower(h.source)=?",('netflix',)).fetchall()]
    groups={}
    for row in rows:
        base,sn=_parse_import_series_title(row.get('name') or '')
        if sn is not None:
            groups.setdefault(normalizar_texto(base),[]).append((row,sn))
    merged_groups=0; removed=0
    for _,items in groups.items():
        if len(items)<2: continue
        target=min(items,key=lambda x:str(x[0].get('created_at') or ''))[0]
        target_id=int(target['id'])
        for row,sn in items:
            rid=int(row['id'])
            if rid==target_id: continue
            parsed_base,_=_parse_import_series_title(row.get('name') or '')
            existing=_season_entries(target)
            # Prefer the per-season rating/cover from the source row when available.
            entry=_season_entry_from_row(row); entry['season']=int(sn or 1); entry['name']=parsed_base
            by={int(e.get('season') or 1):dict(e) for e in existing}
            current=by.get(int(sn or 1),{})
            for k,v in entry.items():
                if v not in (None,'',[],{}):
                    if k=='score' and current.get('score') not in (None,''): continue
                    current[k]=v
            by[int(sn or 1)]=current
            seasons=[by[k] for k in sorted(by)]
            covers=[e.get('cover') for e in seasons if e.get('cover')]
            history_sources=_json_load(target.get('history_sources'),[]) if target else []
            if not isinstance(history_sources,list): history_sources=[]
            if 'netflix' not in history_sources: history_sources.append('netflix')
            conn.execute("UPDATE titles SET name=?,franchise_key=?,season_covers=?,seasons_json=?,total_seasons=?,cover=?,history_sources=? WHERE id=?",
                         (parsed_base,franchise_key_from_title(parsed_base),json.dumps(covers,ensure_ascii=False),json.dumps(seasons,ensure_ascii=False),len(seasons),target.get('cover') or (covers[0] if covers else ''),json.dumps(history_sources,ensure_ascii=False),target_id))
            conn.execute('UPDATE imported_history SET title_id=? WHERE title_id=?',(target_id,rid))
            conn.execute('UPDATE watch_history SET title_id=? WHERE title_id=?',(target_id,rid))
            conn.execute('DELETE FROM titles WHERE id=?',(rid,))
            removed+=1
            target=dict(conn.execute('SELECT * FROM titles WHERE id=?',(target_id,)).fetchone())
        # Final normalization for the target.
        target=dict(conn.execute('SELECT * FROM titles WHERE id=?',(target_id,)).fetchone())
        base,_=_parse_import_series_title(target.get('name') or '')
        seasons=_season_entries(target)
        covers=[e.get('cover') for e in seasons if e.get('cover')]
        conn.execute("UPDATE titles SET name=?,franchise_key=?,season_covers=?,seasons_json=?,total_seasons=?,cover=? WHERE id=?",
                     (base,franchise_key_from_title(base),json.dumps(covers,ensure_ascii=False),json.dumps(seasons,ensure_ascii=False),len(seasons),target.get('cover') or (covers[0] if covers else ''),target_id))
        merged_groups+=1
    rows=[dict(r) for r in conn.execute("SELECT * FROM titles ORDER BY id ASC").fetchall()]
    dup_groups={}
    for r in rows:
        key=(_norm_name(r.get('name')), str(r.get('category') or '').lower())
        if key[0] and r.get('history_sources') not in (None,'','[]'):
            dup_groups.setdefault(key,[]).append(r)
    for items in dup_groups.values():
        if len(items)<2: continue
        keep=items[0]
        for dup in items[1:]:
            if int(dup['id'])==int(keep['id']): continue
            conn.execute('UPDATE imported_history SET title_id=? WHERE title_id=?',(keep['id'],dup['id']))
            conn.execute('UPDATE watch_history SET title_id=? WHERE title_id=?',(keep['id'],dup['id']))
            if not keep.get('cover') and dup.get('cover'):
                conn.execute('UPDATE titles SET cover=? WHERE id=?',(dup.get('cover'),keep['id']))
            conn.execute('DELETE FROM titles WHERE id=?',(dup['id'],))
            removed+=1
    conn.commit(); conn.close()
    return {'groups_merged':merged_groups,'titles_removed':removed}

def import_external_history(source, entries):
    if source not in ('netflix','tomato'):
        raise ValueError('Fonte não suportada.')
    clean=[]
    for e in list(entries or [])[:10000]:
        title=str(e.get('title') or e.get('name') or '').strip()
        if not title: continue
        clean.append({'title':title,'watched_at':str(e.get('watched_at') or e.get('date') or '').strip(),'profile':str(e.get('profile') or '').strip(),'season':_int_or_none(e.get('season'),None),'episode':_int_or_none(e.get('episode'),None)})
    if not clean: return {'source':source,'imported':0,'created':0,'linked':0,'skipped':0,'errors':0}
    grouped={}
    for e in clean:
        base,detected=_parse_import_series_title(e['title'])
        e['canonical_title']=base
        if e.get('season') is None and detected is not None: e['season']=detected
        grouped.setdefault(normalizar_texto(base),[]).append(e)
    created=linked=skipped=imported=errors=0
    metadata_cache={}
    for _,group in grouped.items():
        representative=group[0]['canonical_title']
        c=db()
        unseen=False
        for e0 in group:
            seen_row=c.execute('SELECT 1 FROM imported_history WHERE source=? AND source_title=? AND watched_at=? AND profile=?',(source,e0['title'],e0['watched_at'],e0['profile'])).fetchone()
            if not seen_row:
                unseen=True; break
        tid=_find_import_title_id(c,representative)
        row=dict(c.execute('SELECT * FROM titles WHERE id=?',(tid,)).fetchone()) if tid else None
        c.close()
        if not unseen:
            continue

        category=_infer_import_category(representative,group)
        # Fast metadata lookup: one cached Jikan request at most per imported franchise.
        # It supplies episode/season totals and a cover when available, without blocking
        # the import on deeper research providers.
        meta=metadata_cache.get(normalizar_texto(representative))
        if meta is None and (not row or not row.get('episodes') or not row.get('cover')):
            try:
                _diag=[]
                _res,_best=smart_search_title(representative, category_hint=category, diagnostics=_diag, fast_mode=True)
                meta=_best or {}
            except Exception:
                meta={}
            metadata_cache[normalizar_texto(representative)]=meta

        if not tid:
            try:
                latest=max((g.get('watched_at') for g in group if g.get('watched_at')),default='')
                total_ep=_int_or_none(meta.get('episodes'),None) if isinstance(meta,dict) else None
                season_num=_int_or_none(meta.get('season_number'),None) if isinstance(meta,dict) else None
                first_watch=min((g.get('watched_at') for g in group if g.get('watched_at')),default='')
                row=add_title({'name':representative,'category':category,'status':'Assistindo','notes':f'Importado do histórico de {source.capitalize()}.','start_date':first_watch,'end_date':'','current_episode':0,'current_season':season_num or 1,'episodes':total_ep,'total_seasons':1,'favorite':0,'last_watched_at':latest,'cover':(meta.get('cover') if isinstance(meta,dict) else '') or ''})
                tid=int(row['id']); created+=1
            except Exception:
                errors+=1; continue

        c=db(); row=dict(c.execute('SELECT * FROM titles WHERE id=?',(tid,)).fetchone())
        existing=_season_entries(row)
        by={int(e.get('season') or 1):dict(e) for e in existing}
        explicit_episodes=[]
        for sn in sorted({int(e.get('season')) for e in group if e.get('season')}):
            chunk=[e for e in group if int(e.get('season') or 1)==sn]
            ent=by.get(sn,{'season':sn,'name':representative,'title_english':'','episodes':None,'score':None,'status':'Assistindo','year':None,'season_name':'','cover':row.get('cover') or '','source_id':'','synopsis':'','synopsis_pt':''})
            ent['season']=sn; ent['name']=representative; ent['watched_count']=len(chunk); ent['last_watched_at']=max((x.get('watched_at') for x in chunk if x.get('watched_at')),default=ent.get('last_watched_at',''))
            if meta and sn == int(meta.get('season_number') or sn):
                if ent.get('episodes') in (None,'') and meta.get('episodes') is not None: ent['episodes']=meta.get('episodes')
                if not ent.get('cover') and meta.get('cover'): ent['cover']=meta.get('cover')
            for x in chunk:
                if x.get('episode'): explicit_episodes.append((sn,int(x['episode'])))
            by[sn]=ent
        if not by:
            # For a plain film/history row, create one synthetic season only when useful to the UI.
            n=int(meta.get('season_number') or 1) if isinstance(meta,dict) else 1
            by[n]={'season':n,'name':representative,'title_english':(meta.get('title_english') if isinstance(meta,dict) else '') or '','episodes':(_int_or_none(meta.get('episodes'),None) if isinstance(meta,dict) else None),'score':None,'status':'Assistindo','year':(_int_or_none(meta.get('year'),None) if isinstance(meta,dict) else None),'season_name':(meta.get('season') if isinstance(meta,dict) else '') or '','cover':(meta.get('cover') if isinstance(meta,dict) else '') or row.get('cover') or '','source_id':str(meta.get('id') or '') if isinstance(meta,dict) else '','synopsis':'','synopsis_pt':''}
        seasons=[by[k] for k in sorted(by)]

        # Infer progress from imported episode rows when the source provides them.
        for season_item in seasons:
            sn=int(season_item.get('season') or 1)
            eps_total=_int_or_none(season_item.get('episodes'),None)
            explicit=[ep for ss,ep in explicit_episodes if ss==sn]
            if explicit:
                season_item['watched_count']=max(int(season_item.get('watched_count') or 0),len(set(explicit)))
                season_item['current_episode']=max(explicit)
                season_item['status']='Concluído' if eps_total and max(explicit)>=eps_total else 'Assistindo'
            else:
                # Netflix/Tomato often provide one row per viewed episode but no explicit number.
                # Count the rows for shows as watched episodes; never mark a movie complete from
                # a single history row because a viewing session may have been partial.
                wc=int(season_item.get('watched_count') or 0)
                if str(category).lower() in ('anime','série','serie','desenho'):
                    season_item['current_episode']=min(wc,eps_total) if eps_total else wc
                    season_item['status']='Concluído' if eps_total and wc>=eps_total else ('Assistindo' if wc>0 else 'Quero assistir')
                else:
                    season_item['current_episode']=0
                    season_item['status']='Assistindo' if wc>0 else 'Quero assistir'

        # Overall series status is based on known season completion, while movies with a single
        # history row remain “Assistindo” unless the source supplies explicit completion data.
        known=[e for e in seasons if _int_or_none(e.get('episodes'),None)]
        all_known_complete=bool(known) and all(int(e.get('current_episode') or 0)>=int(e.get('episodes') or 0) for e in known)
        # Qualquer registro do histórico representa consumo iniciado. Só marcamos
        # como concluído quando o total conhecido foi efetivamente atingido.
        overall_status='Concluído' if all_known_complete else ('Assistindo' if group else 'Quero assistir')

        covers=[e.get('cover') for e in seasons if e.get('cover')]
        history_sources=_json_load(row.get('history_sources'),[]) if row else []
        if not isinstance(history_sources,list): history_sources=[]
        if source not in history_sources: history_sources.append(source)
        latest=max((e.get('watched_at') for e in group if e.get('watched_at')),default=row.get('last_watched_at') or '')
        first_watch=min((e.get('watched_at') for e in group if e.get('watched_at')),default=row.get('start_date') or '')
        total_episodes=sum(int(e.get('episodes') or 0) for e in seasons) or _int_or_none(row.get('total_episodes'),None)
        current_season=max((int(e.get('season') or 1) for e in seasons if int(e.get('current_episode') or 0)>0),default=int(row.get('current_season') or 1))
        current_ep=max((int(e.get('current_episode') or 0) for e in seasons if int(e.get('season') or 1)==current_season),default=0)
        end_date=latest if overall_status=='Concluído' else row.get('end_date') or ''
        updates=(representative,row.get('category') or category,franchise_key_from_title(representative),json.dumps(covers,ensure_ascii=False),json.dumps(seasons,ensure_ascii=False),len(seasons),row.get('cover') or (covers[0] if covers else ''),json.dumps(history_sources,ensure_ascii=False),latest,first_watch,end_date,total_episodes,current_season,current_ep,overall_status,tid)
        c.execute('UPDATE titles SET name=?,category=?,franchise_key=?,season_covers=?,seasons_json=?,total_seasons=?,cover=?,history_sources=?,last_watched_at=?,start_date=COALESCE(NULLIF(start_date,""),?),end_date=?,total_episodes=?,current_season=?,current_episode=?,status=? WHERE id=?',updates)
        for e in group:
            when=e['watched_at']; profile=e['profile']
            exists=c.execute('SELECT 1 FROM imported_history WHERE source=? AND source_title=? AND watched_at=? AND profile=?',(source,e['title'],when,profile)).fetchone()
            if exists:
                skipped+=1; continue
            now=datetime.now().isoformat(timespec='seconds')
            c.execute('INSERT INTO imported_history(source,source_title,watched_at,profile,season,episode,title_id,created_at) VALUES(?,?,?,?,?,?,?,?)',(source,e['title'],when,profile,e.get('season'),e.get('episode'),tid,now)); imported+=1
            if when: c.execute('UPDATE titles SET last_watched_at=? WHERE id=?',(when,tid))
            ep=e.get('episode')
            if ep and int(ep)>0:
                season=int(e.get('season') or 1); c.execute('INSERT OR IGNORE INTO watch_history(title_id,season,episode,watched_at) VALUES(?,?,?,?)',(tid,season,int(ep),when or now))
        c.commit(); c.close()
    try: consolidation=consolidate_imported_seasons()
    except Exception: consolidation={'groups_merged':0,'titles_removed':0}
    return {'source':source,'imported':imported,'created':created,'linked':linked,'skipped':skipped,'errors':errors,'unique_titles':len(grouped),**consolidation}

def imported_history_rows():
    conn=db(); rows=[dict(r) for r in conn.execute('SELECT * FROM imported_history ORDER BY created_at DESC LIMIT 500').fetchall()]; conn.close(); return rows

def add_title(data):
    name = str(data.get("name", "")).strip()
    if not name:
        raise ValueError("Nome é obrigatório.")

    rating = data.get("rating")
    try:
        rating = float(rating) if rating not in (None, "") else None
        if rating is not None and not 0 <= rating <= 10:
            rating = None
    except Exception:
        rating = None

    episodes = _int_or_none(data.get("episodes"), None)
    current_episode = _int_or_none(data.get("current_episode"), 0)
    current_season = max(1, _int_or_none(data.get("current_season"), 1))
    total_seasons = _int_or_none(data.get("total_seasons"), 1)
    favorite = 1 if str(data.get("favorite", "0")).lower() in ("1", "true", "yes", "sim") else 0
    status = data.get("status", "Quero assistir")
    source_id = str(data.get("source_id", ""))
    existing = find_existing_title(name, source_id) or find_franchise_title(name)
    if existing:
        return group_existing_title_data(existing["id"], data)
    # Preserve the complete season editor when a title is created.
    incoming_seasons = _sanitize_seasons_from_payload({"seasons_json": "[]"}, data.get("seasons_json"))
    if not incoming_seasons:
        incoming_seasons = [{"season": season_number_from_title(name), "name": name, "title_english": data.get("title_english", ""), "episodes": episodes, "score": rating, "status": status, "year": _int_or_none(data.get("year"), None), "season_name": data.get("season", ""), "cover": data.get("cover", ""), "source_id": source_id, "synopsis": data.get("synopsis", ""), "synopsis_pt": data.get("synopsis_pt", "")} ]
    incoming_seasons = _merge_season_entries(incoming_seasons)
    total_seasons = max(int(total_seasons or 1), len(incoming_seasons))
    total_episodes = sum(int(e.get("episodes") or 0) for e in incoming_seasons) or episodes
    season_covers = [e.get("cover") for e in incoming_seasons if e.get("cover")]
    start_date, end_date = _auto_datas(status, data.get("start_date", ""), data.get("end_date", ""))
    if episodes is not None:
        current_episode = min(current_episode, episodes)
    if status == "Concluído" and episodes:
        current_episode = episodes

    conn = db()
    cur = conn.execute("""
        INSERT INTO titles(
            name, category, status, rating, start_date, end_date, cover,
            notes, episodes, current_episode, current_season, total_seasons,
            favorite, last_watched_at, watch_rate, completed_at, title_english, title_japanese, synopsis, synopsis_pt,
            year, season, duration, genres, source_id, franchise_key, season_covers, seasons_json, total_episodes, local_cover_photos, cover_collage, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        name,
        data.get("category", "Anime"),
        status,
        rating,
        start_date,
        end_date,
        (build_cover_collage(_safe_local_photo_paths(data.get("local_cover_photos"))) or data.get("cover", "")),
        data.get("notes", ""),
        episodes,
        current_episode,
        current_season,
        total_seasons,
        favorite,
        datetime.now().isoformat(timespec="seconds") if current_episode else "",
        float(data.get("watch_rate") or 2),
        end_date if status == "Concluído" else "",
        data.get("title_english", ""),
        data.get("title_japanese", ""),
        data.get("synopsis", ""),
        data.get("synopsis_pt", ""),
        _int_or_none(data.get("year"), None),
        data.get("season", ""),
        data.get("duration", ""),
        ", ".join(map(str, data.get("genres", []))) if isinstance(data.get("genres"), (list, tuple)) else str(data.get("genres", "")),
        str(data.get("source_id", "")),
        franchise_key_from_title(name),
        json.dumps(season_covers, ensure_ascii=False),
        json.dumps(incoming_seasons, ensure_ascii=False),
        total_episodes,
        json.dumps(_safe_local_photo_paths(data.get("local_cover_photos")), ensure_ascii=False),
        build_cover_collage(_safe_local_photo_paths(data.get("local_cover_photos"))),
        datetime.now().isoformat(timespec="seconds")
    ))
    conn.commit()
    row = conn.execute("SELECT * FROM titles WHERE id = ?", (cur.lastrowid,)).fetchone()
    conn.close()
    return dict(row)


def record_watch_history(title_id, season, episode):
    episode=int(episode or 0)
    if episode <= 0:
        return
    conn=db()
    conn.execute("INSERT OR IGNORE INTO watch_history(title_id, season, episode, watched_at) VALUES(?,?,?,?)", (int(title_id), int(season or 1), episode, datetime.now().isoformat(timespec="seconds")))
    conn.commit(); conn.close()

def record_watch_progress(title_id, season, old_episode, new_episode):
    old_episode=max(0,int(old_episode or 0)); new_episode=max(0,int(new_episode or 0))
    if new_episode <= old_episode:
        return
    conn=db(); now=datetime.now().isoformat(timespec="seconds")
    for ep in range(old_episode+1, new_episode+1):
        conn.execute("INSERT OR IGNORE INTO watch_history(title_id, season, episode, watched_at) VALUES(?,?,?,?)", (int(title_id), int(season or 1), ep, now))
    conn.commit(); conn.close()


def title_history(title_id):
    conn=db()
    rows=conn.execute("SELECT season,episode,watched_at FROM watch_history WHERE title_id=? ORDER BY watched_at DESC LIMIT 20", (int(title_id),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _sync_season_statuses(entries, current_season, overall_status, current_episode=0):
    """Mantém os status das temporadas coerentes com a posição atual."""
    cur=max(1,int(current_season or 1)); ep=max(0,int(current_episode or 0)); out=[]
    for item in entries:
        e=dict(item)
        try:n=max(1,int(e.get("season") or 1))
        except Exception:n=1
        eps=_int_or_none(e.get("episodes"),None)
        if n < cur and eps:
            e["status"]="Concluído"
        elif n == cur:
            if overall_status == "Aguardando":
                e["status"]="Aguardando"
            elif overall_status == "Concluído" and eps and ep >= eps:
                e["status"]="Concluído"
            elif ep > 0 or overall_status == "Assistindo":
                e["status"]="Assistindo"
        elif n > cur and e.get("status") == "Assistindo":
            e["status"]="Quero assistir"
        out.append(e)
    return out

def update_title(title_id, data):
    atual = get_title(title_id)
    if not atual:
        return None

    old_episode = int(atual.get("current_episode") or 0)
    old_season = int(atual.get("current_season") or 1)

    name = str(data.get("name", atual["name"])).strip()
    if not name:
        raise ValueError("Nome é obrigatório.")

    rating = data.get("rating", atual.get("rating"))
    try:
        rating = float(rating) if rating not in (None, "") else None
        if rating is not None and not 0 <= rating <= 10:
            rating = None
    except Exception:
        rating = atual.get("rating")

    seasons = _sanitize_seasons_from_payload(atual, data.get("seasons_json"))
    if seasons:
        current_info = next((e for e in seasons if int(e.get("season") or 1)==int(data.get("current_season", atual.get("current_season") or 1))), None)
    else:
        current_info = None
    season1 = next((e for e in seasons if int(e.get("season") or 1)==1), None)
    episodes_default = (current_info or {}).get("episodes") or (season1 or {}).get("episodes") or atual.get("episodes")
    episodes = _int_or_none(data.get("episodes", episodes_default), episodes_default)
    current_episode = _int_or_none(data.get("current_episode", atual.get("current_episode")), atual.get("current_episode") or 0)
    current_season = max(1, _int_or_none(data.get("current_season", atual.get("current_season")), atual.get("current_season") or 1))
    total_seasons = _int_or_none(data.get("total_seasons", len(seasons) or atual.get("total_seasons")), len(seasons) or atual.get("total_seasons"))
    if seasons and data.get("seasons_json") not in (None, ""):
        total_seasons=max(total_seasons or 1, len(seasons))
    favorite = 1 if str(data.get("favorite", atual.get("favorite", 0))).lower() in ("1", "true", "yes", "sim") else 0
    try:
        watch_rate = float(data.get("watch_rate", atual.get("watch_rate", 2)) or 2)
        if watch_rate <= 0: watch_rate = 2
    except Exception:
        watch_rate = float(atual.get("watch_rate") or 2)
    status = data.get("status", atual.get("status", "Quero assistir"))
    start_date, end_date = _auto_datas(status, data.get("start_date", atual.get("start_date")), data.get("end_date", atual.get("end_date")))

    # Keep the episode count relative to the current season and automatically advance seasons.
    if seasons and ("current_episode" in data or "current_season" in data):
        progress_row = dict(atual); progress_row["seasons_json"] = json.dumps(seasons, ensure_ascii=False); current_season, current_episode, normalized_status = _normalize_progress_position(progress_row, current_season, current_episode)
        if status in ("Quero assistir", "Assistindo", "Concluído", "Aguardando"):
            status = normalized_status if current_episode or normalized_status != "Quero assistir" else status
    if episodes is not None:
        current_episode = min(current_episode, episodes)
    if status == "Concluído" and episodes and not _next_known_season(atual, current_season):
        current_episode = episodes
    seasons = _sync_season_statuses(seasons, current_season, status, current_episode)
    completed_at = atual.get("completed_at", "")
    if status == "Concluído":
        completed_at = end_date or hoje_br()
    elif status != "Concluído":
        completed_at = ""
    if current_episode > 0 and not start_date:
        start_date = hoje_br()
    last_watched = atual.get("last_watched_at", "")
    if "current_episode" in data or "current_season" in data:
        last_watched = datetime.now().isoformat(timespec="seconds")

    local_photos = _safe_local_photo_paths(data.get('local_cover_photos', atual.get('local_cover_photos', '[]')))
    local_collage = build_cover_collage(local_photos) if local_photos else atual.get('cover_collage', '')
    conn = db()
    conn.execute("""
        UPDATE titles SET
            name=?, category=?, status=?, rating=?, start_date=?, end_date=?, cover=?, notes=?,
            episodes=?, current_episode=?, current_season=?, total_seasons=?, favorite=?,
            last_watched_at=?, watch_rate=?, completed_at=?, title_english=?, title_japanese=?, synopsis=?, synopsis_pt=?, year=?, season=?,
            duration=?, genres=?, source_id=?, season_covers=?, seasons_json=?, total_episodes=?, local_cover_photos=?, cover_collage=?
        WHERE id=?
    """, (
        name,
        data.get("category", atual.get("category", "Anime")),
        status,
        rating,
        start_date,
        end_date,
        (str(data.get("cover") or "").strip() or atual.get("cover", "")),
        data.get("notes", atual.get("notes", "")),
        episodes,
        current_episode,
        current_season,
        total_seasons,
        favorite,
        last_watched,
        watch_rate,
        completed_at,
        data.get("title_english", atual.get("title_english", "")),
        data.get("title_japanese", atual.get("title_japanese", "")),
        data.get("synopsis", atual.get("synopsis", "")),
        data.get("synopsis_pt", atual.get("synopsis_pt", "")),
        _int_or_none(data.get("year", atual.get("year")), atual.get("year")),
        data.get("season", atual.get("season", "")),
        data.get("duration", atual.get("duration", "")),
        ", ".join(map(str, data.get("genres", []))) if isinstance(data.get("genres"), (list, tuple)) else str(data.get("genres", atual.get("genres", ""))),
        str(data.get("source_id", atual.get("source_id", ""))),
        json.dumps([e.get("cover") for e in seasons if e.get("cover")], ensure_ascii=False),
        json.dumps(seasons, ensure_ascii=False),
        sum(int(e.get("episodes") or 0) for e in seasons) or _int_or_none(data.get("total_episodes", atual.get("total_episodes", episodes)), atual.get("total_episodes", episodes)),
        json.dumps(local_photos, ensure_ascii=False),
        local_collage,
        title_id
    ))
    conn.commit()
    conn.close()

    new_episode = int(current_episode or 0)
    try:
        # Registra avanço normal dentro da temporada.
        if int(current_season or 1) == old_season and new_episode > old_episode:
            record_watch_progress(title_id, old_season, old_episode, new_episode)
        # Ao finalizar uma temporada e entrar na próxima, registre o episódio final da temporada antiga.
        elif int(current_season or 1) > old_season:
            old_info = _season_info(dict(atual), old_season)
            old_total = _int_or_none((old_info or {}).get("episodes"), None)
            if old_total and old_episode < old_total:
                record_watch_progress(title_id, old_season, old_episode, old_total)
            elif old_total and old_episode == old_total:
                record_watch_history(title_id, old_season, old_total)
    except Exception as e:
        print("Histórico ignorado:", e)

    return get_title(title_id)


def automatic_watch_rate():
    stamp = _library_db_stamp()
    key = ('watch_rate', stamp)
    def build():
        conn=db()
        rows=conn.execute("SELECT watched_at, season, episode FROM watch_history WHERE watched_at != '' ORDER BY watched_at DESC LIMIT 1000").fetchall()
        conn.close()
        rows=list(reversed(rows))
        from datetime import datetime as _dt
        points=[]; seen=set(); now=_dt.now()
        for r in rows:
            try: dt=_dt.fromisoformat(str(r["watched_at"]))
            except Exception: continue
            if (now-dt).total_seconds() > 30*86400: continue
            k=(dt.date().isoformat(), int(r["season"] or 1), int(r["episode"] or 0))
            if k in seen: continue
            seen.add(k); points.append(dt)
        if not points:
            return {"rate":2.0,"episodes":0,"days":0,"automatic":False}
        days=max(1,(points[-1].date()-points[0].date()).days+1)
        rate=round(max(0.5,min(20.0,len(points)/days)),1)
        return {"rate":rate,"episodes":len(points),"days":days,"automatic":len(points)>=2}
    return _library_cached(key, build)


def library_stats():
    stamp = _library_db_stamp()
    key = ('stats', stamp)
    def build():
        conn = db()
        total = conn.execute("SELECT COUNT(*) FROM titles").fetchone()[0]
        watching = conn.execute("SELECT COUNT(*) FROM titles WHERE status='Assistindo'").fetchone()[0]
        done = conn.execute("SELECT COUNT(*) FROM titles WHERE status='Concluído'").fetchone()[0]
        wanted = conn.execute("SELECT COUNT(*) FROM titles WHERE status='Quero assistir'").fetchone()[0]
        favorites = conn.execute("SELECT COUNT(*) FROM titles WHERE favorite=1").fetchone()[0]
        episodes_watched = conn.execute("SELECT COUNT(*) FROM watch_history").fetchone()[0]
        episodes_total = conn.execute("SELECT COALESCE(SUM(COALESCE(total_episodes,episodes)),0) FROM titles WHERE episodes IS NOT NULL OR total_episodes IS NOT NULL").fetchone()[0]
        ratings = [r[0] for r in conn.execute("SELECT rating FROM titles WHERE rating IS NOT NULL").fetchall()]
        rows = conn.execute("SELECT current_episode,episodes FROM titles").fetchall()
        progress_total = 0; progress_count = 0
        for r in rows:
            if r[1]:
                progress_total += min(int(r[0] or 0), int(r[1])) / int(r[1])
                progress_count += 1
        conn.close()
        completion = round((progress_total / progress_count) * 100) if progress_count else 0
        return {"total": total, "watching": watching, "done": done, "wanted": wanted,
                "favorites": favorites, "episodes_watched": int(episodes_watched or 0),
                "episodes_total": int(episodes_total or 0),
                "average_rating": round(sum(ratings)/len(ratings), 1) if ratings else None,
                "completion": completion}
    return _library_cached(key, build)


def get_cache_by_id(anime_id):
    return cache_get("id:" + str(anime_id))


def delete_title(title_id):

    atual = get_title(title_id)

    old_episode = int(atual.get("current_episode") or 0) if atual else 0
    old_season = int(atual.get("current_season") or 1) if atual else 1

    if not atual:
        return False

    conn = db()

    for table in ('watch_history','imported_history'):
        try:
            conn.execute(f"DELETE FROM {table} WHERE title_id = ?", (title_id,))
        except Exception:
            pass
    conn.execute(
        """
        DELETE FROM titles
        WHERE id = ?
        """,
        (title_id,)
    )

    conn.commit()
    conn.close()

    return True


# ============================================================
# OCR / LEITURA VISUAL 4.22
# ============================================================

PALAVRAS_IGNORADAS = {
    "comentários","comentarios","curtido","curtir","favoritado","legendado","dublado",
    "season","temporada","episódio","episodio","episódios","episodios","minutos",
    "ação","acao","aventura","fantasia","escolar","home","pesquisar","assistir",
    "assistindo","compartilhar","seguir","seguindo","perfil","likes","like","views",
    "visualizações","visualizacoes","download","baixar","próximo","proximo","anterior",
    "descrição","descricao","sinopse","continue","continuar","clique","clicar","cliquei",
    "veja","ver","confira","assista","agora","aqui","novo","nova","vídeo","video",
    "imagem","tela","selecionar","selecionado","resultado","resultados","apareceu","aparecer",
    "todos","adicionar","identificar","configura","configuração","configuracoes","estatísticas",
    "estatisticas","biblioteca","coleção","colecao","suas","historico","histórico","próximo","proximo"
}

UI_PHRASES = {
    "curtir", "curtido", "favoritado", "comentarios", "comentarios 0", "adicionar", "biblioteca",
    "continuar", "continuar assistindo", "cavaleiros apareceu", "clique", "pesquisar", "configuracoes",
    "estatisticas", "resultado", "resultados", "veja mais", "assista agora", "novo episodio", "novo episódio"
}

TITLE_SIGNAL_WORDS = {
    "tsue","tsurugi","wistoria","wand","sword","fire","force","enen","shouboutai",
    "jujutsu","kaisen","one","piece","solo","leveling","attack","titan","demon","slayer",
    "naruto","bleach","dragon","ball","clover","hero","academia","my","isekai","monogatari"
}

def limpar_linha(texto):
    if not texto: return ""
    texto=re.sub(r"[\r\n\t]+"," ",str(texto).strip())
    texto=re.sub(r"\s+"," ",texto)
    return texto.strip(" -|•·—–")

def normalizar_texto(texto):
    texto=str(texto or "").lower()
    texto=re.sub(r"[^a-z0-9à-ÿ]+"," ",texto)
    return re.sub(r"\s+"," ",texto).strip()

def parece_horario(texto):
    return bool(re.fullmatch(r"\d{1,2}[:.]\d{2}",str(texto).strip()))

def _is_ui_phrase(texto):
    n=normalizar_texto(texto)
    if not n: return True
    if n in UI_PHRASES: return True
    words=n.split()
    if any(w in PALAVRAS_IGNORADAS for w in words) and len(words)<=3:
        return True
    return False

APP_CONTEXT_TERMS = {
    "cime", "mundos", "biblioteca", "colecao", "coleção", "pesquisar", "descobrir",
    "adicionar", "identificar", "imagem", "estatisticas", "estatísticas", "configuracoes",
    "configurações", "assistindo", "concluidos", "concluídos", "favoritos", "generos",
    "gêneros", "historico", "histórico", "continue", "continuar", "recentes", "ritmo",
    "temporada", "episodio", "episódio", "media", "média", "nota", "biblioteca",
}

def _context_penalty(words):
    low=[normalizar_texto(w) for w in words]
    hits=sum(1 for w in low if w in APP_CONTEXT_TERMS)
    return min(65, hits*14)

def _looks_like_sentence(text):
    n=normalizar_texto(text)
    words=n.split()
    if not words: return True
    if len(words) >= 9: return True
    # Frases com verbos/pontuação tendem a ser descrição, legenda ou interface.
    sentence_words={
        "vamos","fazer","de","da","do","das","dos","você","voce","isso",
        "está","esta","não","nao","vai","ser","para","com","que","quando",
        "onde","como","agora","aqui","melhor","melhorar","aparece","apareceu",
        "clique","clicar","abrir","fechar","continuar","pesquisar","assistir",
    }
    hits=sum(1 for w in words if w in sentence_words)
    if hits>=2: return True
    context_hits=sum(1 for w in words if w in APP_CONTEXT_TERMS)
    if context_hits>=2: return True
    if any(w in {"inteligente","encontre","acompanhe","descubra","sobrecarregar","sobrecarregar","tela"} for w in words):
        return True
    if re.search(r"[,!?]", text) and hits>=1: return True
    return False

def _candidate_quality(text, conf=0, height=0, words=None, relative_top=0.5):
    n=normalizar_texto(text)
    ws=n.split()
    if not ws: return -999
    if parece_lixo(text): return -999
    if _looks_like_sentence(text): return -999
    if height < 15 and not any(w in TITLE_SIGNAL_WORDS for w in ws): return -999
    score=float(conf)*0.65
    # Títulos geralmente formam 1-7 palavras e têm um bloco visual relativamente grande.
    if 2<=len(ws)<=6: score += 24
    elif len(ws)==1: score += 6
    if 7<=len(ws)<=8: score -= 12
    if height>=38: score += 22
    elif height>=28: score += 14
    elif height>=20: score += 6
    score += max(0, 10-abs(relative_top-0.45)*15)
    score -= _context_penalty(ws)
    if any(w in TITLE_SIGNAL_WORDS for w in ws): score += 16
    if re.search(r"\b(19|20)\d{2}\b", text): score -= 22
    if sum(ch in text for ch in ".:;")>=2: score -= 10
    return score

def parece_lixo(texto):
    low=normalizar_texto(texto)
    if not low or parece_horario(texto) or _is_ui_phrase(texto): return True
    words=low.split(); letters=sum(c.isalpha() for c in texto); nums=sum(c.isdigit() for c in texto)
    if letters < 4 or nums > letters*2 or len(words)>12: return True
    if re.search(r"\b(19|20)\d{2}\b", low) and len(words)<=4: return True
    ruido={"apareceu","aparecer","clique","clicar","veja","confira","assista","assistir","assistindo","continuar","pesquisar","selecionar","resultado","resultados","curtir","curtido","favoritado"}
    if any(w in ruido for w in words) and len(words)<=8: return True
    if texto.count(',')+texto.count('!')+texto.count('?')>=2 and len(words)<=8: return True
    return False

def _resize_for_ocr(image,max_width=2400):
    image=image.convert("RGB"); w,h=image.size
    if w>max_width:
        r=max_width/w; image=image.resize((max(1,int(w*r)),max(1,int(h*r))),Image.Resampling.LANCZOS)
    return image

def _ocr_pass(image,psm=11):
    try:
        d=pytesseract.image_to_data(
            image,
            lang=OCR_LANGUAGE,
            config=f"--oem 3 --psm {psm} -c preserve_interword_spaces=1",
            output_type=pytesseract.Output.DICT
        )
    except Exception as e:
        print("Erro OCR:",e); return []
    out=[]
    for i,t in enumerate(d.get("text",[])):
        t=limpar_linha(t)
        if not t: continue
        try: conf=float(d["conf"][i])
        except Exception: conf=-1
        if conf<12: continue
        out.append({
            "text":t,"conf":conf,
            "left":int(d["left"][i]),"top":int(d["top"][i]),
            "width":int(d["width"][i]),"height":int(d["height"][i]),
            "line":(d["block_num"][i],d["par_num"][i],d["line_num"][i])
        })
    return out

def _group_ocr_lines(words):
    groups={}
    for w in words: groups.setdefault(w["line"],[]).append(w)
    lines=[]
    for g in groups.values():
        g.sort(key=lambda x:x["left"])
        txt=limpar_linha(" ".join(x["text"] for x in g))
        if txt:
            lines.append((txt,sum(x["conf"] for x in g)/len(g),min(x["top"] for x in g),g))
    return lines

def _prepare_variants(image):
    """Variantes baratas, mas complementares, para títulos com sombra, fundo colorido e compressão."""
    gray=ImageOps.grayscale(image)
    contrast=ImageEnhance.Contrast(gray).enhance(1.8)
    sharp=contrast.filter(ImageFilter.SHARPEN)
    auto=ImageOps.autocontrast(sharp)
    up=gray.resize((min(3200,max(1,gray.width*2)),min(3200,max(1,gray.height*2))),Image.Resampling.LANCZOS)
    up=ImageOps.autocontrast(ImageEnhance.Contrast(up).enhance(1.45))
    # Binarização suave ajuda letras claras/escuras em screenshots sem aumentar demais o custo.
    bw=auto.point(lambda p: 255 if p > 155 else 0)
    return [image, auto, up, bw]

def _smart_title_regions(image):
    """Cria regiões candidatas sem depender de OpenCV: topo, centro e faixa de maior contraste."""
    w,h=image.size
    regions=[]
    bands=[(0.0,0.32),(0.20,0.62),(0.45,0.90)]
    gray=ImageOps.grayscale(image)
    for idx,(a,b) in enumerate(bands):
        y1,y2=int(h*a),int(h*b)
        if y2-y1 >= 30:
            crop=image.crop((0,y1,w,y2))
            regions.append((f"band_{idx}",crop,y1))
    # Uma região central larga costuma conter logos/títulos sobre personagens.
    y1=int(h*0.22); y2=int(h*0.72)
    regions.append(("title_center",image.crop((0,y1,w,y2)),y1))
    return regions

def _candidate_windows(words):
    """Monta grupos de palavras próximos para recuperar títulos quebrados pelo OCR."""
    if not words: return []
    seq=sorted(words,key=lambda x:(x["top"],x["left"]))
    out=[]
    for i in range(len(seq)):
        chunk=[]
        for j in range(i,min(i+10,len(seq))):
            w=seq[j]
            if chunk and abs(w["top"]-chunk[-1]["top"])>max(
                34,int((w["height"]+chunk[-1]["height"])/2)*2
            ):
                break
            chunk.append(w)
            txt=limpar_linha(" ".join(x["text"] for x in chunk))
            if 2<=len(txt.split())<=8 and 5<=len(txt)<=80:
                conf=sum(x["conf"] for x in chunk)/len(chunk)
                out.append((txt,conf,chunk[0]["top"],chunk))
    return out

def _candidate_quality(text, conf=0, height=0, words=None, relative_top=0.5):
    n=normalizar_texto(text)
    ws=n.split()
    if not ws: return -999
    if parece_lixo(text): return -999
    sentence_like = _looks_like_sentence(text)
    # Títulos muito longos (novels/adaptações) podem passar de 9 palavras.
    # Só liberamos esse caso quando o texto veio de um bloco visual grande e confiável.
    if sentence_like:
        long_title_exception = len(ws) >= 9 and height >= 34 and float(conf) >= 58 and not re.search(r'[,!?]{2,}', text)
        if not long_title_exception:
            return -999
    if height<15 and not any(w in TITLE_SIGNAL_WORDS for w in ws): return -999

    score=float(conf)*0.68
    if 3<=len(ws)<=6:
        score += 31
    elif len(ws)==2:
        score += 20
    elif len(ws)==1:
        score += 7
    elif 7<=len(ws)<=9:
        score -= 4
    elif 10<=len(ws)<=14:
        score += 4 if height >= 34 else -6
    else:
        score -= 20

    if height>=52: score += 28
    elif height>=38: score += 22
    elif height>=28: score += 14
    elif height>=20: score += 7

    score += max(0,10-abs(relative_top-0.42)*18)
    score -= _context_penalty(ws)
    if any(w in TITLE_SIGNAL_WORDS for w in ws): score += 18
    if re.search(r"\b(19|20)\d{2}\b",text): score -= 22
    if sum(ch in text for ch in ".:;")>=2: score -= 10
    return score

def _assemble_large_text_runs(words):
    """Reconstrói títulos quebrados em várias linhas/blocos usando palavras maiores."""
    big=[w for w in words if w.get("height",0)>=24 and w.get("conf",0)>=45]
    if not big: return []
    big=sorted(big,key=lambda x:(x["top"],x["left"]))
    lines=[]
    for w in big:
        center=w["top"]+w.get("height",0)/2
        placed=False
        for line in lines:
            if abs(center-line["center"]) <= max(26, w.get("height",0)*0.75):
                line["words"].append(w); line["center"]=(line["center"]+center)/2; placed=True; break
        if not placed:
            lines.append({"center":center,"words":[w]})
    for line in lines: line["words"].sort(key=lambda x:x["left"])
    lines.sort(key=lambda x:x["center"])
    out=[]
    for i in range(len(lines)):
        group=[]; last=None
        for j in range(i,min(i+4,len(lines))):
            cur=lines[j]
            if last is not None and cur["center"]-last > 95: break
            group.append(cur); last=cur["center"]
            txt=limpar_linha(" ".join(limpar_linha(" ".join(w["text"] for w in ln["words"])) for ln in group))
            if 2<=len(txt.split())<=18 and len(txt)<=150:
                conf=sum(w["conf"] for ln in group for w in ln["words"])/max(1,sum(len(ln["words"]) for ln in group))
                height=max((w.get("height",0) for ln in group for w in ln["words"]),default=0)
                top=min((w["top"] for ln in group for w in ln["words"]),default=0)
                out.append((txt,conf,height,top))
    return out[:30]


def _evaluate_ocr_words(words,image_h):
    if not words: return []
    lines=[]
    for txt,conf,top,line_words in _group_ocr_lines(words):
        heights=[max(1,x.get("height",0)) for x in line_words]
        height=max(heights) if heights else 0
        rel=top/max(1,image_h)
        score=_candidate_quality(txt,conf,height,line_words,rel)
        if score>-999:
            lines.append((score,txt,conf,height,top,line_words))
    for txt,conf,top,chunk in _candidate_windows(words):
        height=max((x.get("height",0) for x in chunk),default=0)
        rel=top/max(1,image_h)
        score=_candidate_quality(txt,conf,height,chunk,rel)-3
        if score>-999:
            lines.append((score,txt,conf,height,top,chunk))
    lines.sort(key=lambda x:x[0],reverse=True)

    out=[];seen=set()
    for item in lines:
        score,txt,*_=item
        key=normalizar_texto(txt)
        if not key or key in seen: continue
        threshold=58 if len(key.split())>=2 else 68
        if score<threshold: continue
        seen.add(key)
        out.append(item)
        if len(out)>=12: break
    return out

def _ocr_candidate_text(items):
    return "\n".join(x[1] for x in items[:8])

def read_image_anchor_text(image_bytes):
    """OCR rápido para a primeira identificação.
    Primeiro lê a imagem inteira em até 1280 px; só faz recortes extras
    quando o primeiro passe não encontrou uma pista forte.
    """
    cached=_ocr_cache_get(image_bytes,"anchor")
    if cached is not None:
        return str(cached)
    try:
        image=_resize_for_ocr(ImageOps.exif_transpose(Image.open(BytesIO(image_bytes))),max_width=1280)
        text=pytesseract.image_to_string(image,lang=OCR_LANGUAGE,config='--oem 3 --psm 6 -c preserve_interword_spaces=1')
        text=str(text or '').strip()
        low=normalizar_texto(text)
        known=canonical_title_from_text(text) or ''
        season_hit=infer_season_from_title_text(text)

        # Se já temos título ou temporada, não gastamos uma segunda bateria de OCR.
        if known or (isinstance(season_hit,dict) and season_hit.get('season')):
            result=((known+' ') if known else '')+text
            result=result[:3600]
            _ocr_cache_put(image_bytes,result,"anchor")
            return result

        # Fallback barato: duas regiões em paralelo apenas quando o quadro inteiro falhou.
        w,h=image.size
        crops=[image.crop((0,0,w,int(h*0.55))), image.crop((0,int(h*0.45),w,h))]
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
            fs=[ex.submit(pytesseract.image_to_string,c,lang=OCR_LANGUAGE,config='--oem 3 --psm 6') for c in crops]
            parts=[]
            for f in fs:
                try: parts.append(f.result())
                except Exception: pass
        text=' '.join([text,*parts]).strip()
        known=canonical_title_from_text(text) or ''
        if known:
            text=known+' '+text
        result=text[:3600]
        _ocr_cache_put(image_bytes,result,"anchor")
        return result
    except Exception:
        return ""

def read_image_candidates(image_bytes):
    """OCR multi-pass: regiões prováveis + variantes + ranking de título. Evita dezenas de passes redundantes."""
    cached=_ocr_cache_get(image_bytes,"deep")
    if cached is not None:
        return list(cached) if isinstance(cached,list) else []
    try:
        image=_resize_for_ocr(ImageOps.exif_transpose(Image.open(BytesIO(image_bytes))),max_width=2200)
        w,h=image.size
        all_words=[]

        jobs=[("full",image,0,11),("full_block",image,0,6)]
        jobs.extend((name,crop,yoff,11) for name,crop,yoff in _smart_title_regions(image))

        # Variantes apenas nas duas regiões de maior valor: evita custo explosivo.
        regions=_smart_title_regions(image)
        for idx,(name,crop,yoff) in enumerate(regions[:1]):
            for vidx,var in enumerate(_prepare_variants(crop)[1:]):
                jobs.append((f"{name}_v{vidx}",var,yoff,11))

        import concurrent.futures
        max_workers=min(6,max(2,(os.cpu_count() or 4)//2))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures=[ex.submit(_ocr_pass,img,psm) for _,img,_,psm in jobs]
            for (name,_,yoff,_),fut in zip(jobs,futures):
                try:
                    words=fut.result()
                    for q in words:
                        q["top"]+=yoff
                        q["_pass"]=name
                    all_words.extend(words)
                except Exception as e:
                    print("OCR pass ignorado:",name,e)

        # Deduplicação espacial mais tolerante entre crops sobrepostos.
        dedup=[]; seen=[]
        for q in sorted(all_words,key=lambda x:(x["top"],x["left"],-x["conf"])):
            nk=normalizar_texto(q["text"])
            if not nk: continue
            duplicate=False
            for old in seen[-120:]:
                if nk==old[0] and abs(q["left"]-old[1])<30 and abs(q["top"]-old[2])<30:
                    duplicate=True; break
            if duplicate: continue
            seen.append((nk,q["left"],q["top"])); dedup.append(q)

        ranked=_evaluate_ocr_words(dedup,h)
        raw="\n".join(q["text"] for q in dedup)
        assembled=_assemble_large_text_runs(dedup)
        assembled_keys={normalizar_texto(x[0]) for x in assembled}

        # Não dependemos de uma única linha: cada linha/bloco passa pelo resolvedor
        # de títulos conhecidos. Isso corrige casos em que o logo aparece no 9º/10º
        # candidato do OCR e seria perdido pelo corte anterior.
        candidates=[]
        known_front=[]
        def add_candidate(txt):
            if txt:
                candidates.append(txt)
                try:
                    ktitle=canonical_title_from_text(txt)
                    if ktitle:
                        known_front.append(ktitle)
                except Exception:
                    pass

        for txt in _extract_known_title(raw):
            add_candidate(txt)
        for txt,conf,height,top in assembled:
            if _candidate_quality(txt,conf,height,None,top/max(1,h)) > -999:
                add_candidate(txt)
        for txt,conf,top,line_words in _group_ocr_lines(dedup):
            if conf>=70 and 1<=len(txt.split())<=9 and not _looks_like_sentence(txt):
                add_candidate(txt)
        for score,txt,conf,height,top,line_words in ranked:
            add_candidate(txt)
        # Títulos clássicos de 1 palavra não devem ser descartados.
        for txt,conf,top,line_words in _group_ocr_lines(dedup):
            n=normalizar_texto(txt)
            if conf>=68 and len(n.split())==1 and len(n)>=4 and not _is_ui_phrase(n):
                add_candidate(txt)

        result=[];keys=set()
        # Primeiro vêm os títulos que foram reconhecidos por regra/alias/fuzzy.
        # Só depois entram linhas genéricas. Isto evita texto promocional engolir
        # o nome real do anime.
        ordered_candidates=[]
        for c in known_front + candidates:
            if c not in ordered_candidates:
                ordered_candidates.append(c)
        for c in ordered_candidates:
            c=limpar_linha(c)
            if not c: continue
            canonical=canonical_title_from_text(c)
            value=canonical or c
            k=normalizar_texto(value)
            if not k or k in keys: continue
            if not canonical and k not in assembled_keys and (_is_ui_phrase(value) or _looks_like_sentence(value)): continue
            keys.add(k); result.append(value)
            if len(result)>=12: break
        # Fallback tardio e dirigido: só roda a leitura de âncora se o OCR profundo
        # não conseguiu produzir nenhum título conhecido. Isso evita pagar o custo
        # extra na maioria das imagens, mas recupera pôsteres difíceis como logos
        # estilizados/cortados.
        if not any(canonical_title_from_text(x) for x in result):
            anchor = read_image_anchor_text(image_bytes)
            ak = canonical_title_from_text(anchor)
            if ak and ak not in result:
                result.insert(0, ak)
        _ocr_cache_put(image_bytes,result,"deep")
        return result
    except Exception as e:
        print("Erro no OCR:",e)
        return []

def read_image_text(image_bytes):
    return "\n".join(read_image_candidates(image_bytes)[:8])

# Títulos oficiais/aliases: NUNCA traduzir o título para pesquisar.
TITLE_ALIASES = {
    # Correções clássicas de OCR e títulos usados no Brasil.
    "fire force": "Fire Force",
    "enen no shouboutai": "Fire Force",
    "combustao humana": "Fire Force",
    "combustão humana": "Fire Force",
    "forca de fogo": "Fire Force",
    "força de fogo": "Fire Force",
    "bombeiros de fogo": "Fire Force",
    "wistoria wand and sword": "Wistoria: Wand and Sword",
    "tsue to tsurugi no wistoria": "Wistoria: Wand and Sword",
    "demon slayer": "Demon Slayer: Kimetsu no Yaiba",
    "kimetsu no yaiba": "Demon Slayer: Kimetsu no Yaiba",
    "demon layer": "Demon Slayer: Kimetsu no Yaiba",
    "swordsmith village": "Demon Slayer: Kimetsu no Yaiba",
    "swordsmith vil": "Demon Slayer: Kimetsu no Yaiba",
    "jordsmith village": "Demon Slayer: Kimetsu no Yaiba",
    "ordsmith villages": "Demon Slayer: Kimetsu no Yaiba",
    "ordsmith village": "Demon Slayer: Kimetsu no Yaiba",
    "jordon smith village": "Demon Slayer: Kimetsu no Yaiba",
    "my hero academia": "My Hero Academia",
    "boku no hero academia": "My Hero Academia",
    "jujutsu kaisen": "Jujutsu Kaisen",
    "one piece": "One Piece",
    "solo leveling": "Solo Leveling",
    "attack on titan": "Attack on Titan",
    "shingeki no kyojin": "Attack on Titan",
    "naruto": "Naruto",
    "naruto shippuden": "Naruto: Shippuden",
    "bleach": "Bleach",
    "black clover": "Black Clover",
    "dragon ball": "Dragon Ball",
    "dragonball": "Dragon Ball",
    "hunter x hunter": "Hunter x Hunter",
    "death note": "Death Note",
    "spy x family": "SPY x FAMILY",
    "chainsaw man": "Chainsaw Man",
    "blue lock": "Blue Lock",
    "haikyuu": "Haikyu!!",
    "haikyu": "Haikyu!!",
    "tokyo revengers": "Tokyo Revengers",
    "dandadan": "DAN DA DAN",
    "dan da dan": "DAN DA DAN",
    "frieren": "Frieren: Beyond Journey's End",
    "sousou no frieren": "Frieren: Beyond Journey's End",
    "oshi no ko": "[Oshi no Ko]",
    "mushoku tensei": "Mushoku Tensei: Jobless Reincarnation",
    "re zero": "Re:ZERO -Starting Life in Another World-",
    "rezero": "Re:ZERO -Starting Life in Another World-",
    "that time i got reincarnated as a slime": "That Time I Got Reincarnated as a Slime",
    "tensura": "That Time I Got Reincarnated as a Slime",
    "one punch man": "One-Punch Man",
    "mob psycho 100": "Mob Psycho 100",
    "vinland saga": "VINLAND SAGA",
    "dr stone": "Dr. STONE",
    "dr stone": "Dr. STONE",
    "jojos bizarre adventure": "JoJo's Bizarre Adventure",
    "jojo bizarre adventure": "JoJo's Bizarre Adventure",
    "the promised neverland": "The Promised Neverland",
    "classroom of the elite": "Classroom of the Elite",
    "kaguya sama love is war": "Kaguya-sama: Love is War",
    "komi cant communicate": "Komi Can't Communicate",
    "spy family": "SPY x FAMILY",
    "black butler": "Black Butler",
    "blue exorcist": "Blue Exorcist",
    "sakamoto days": "SAKAMOTO DAYS",
    "kaiju no 8": "Kaiju No. 8",
    "kaiju 8": "Kaiju No. 8",
    "wind breaker": "WIND BREAKER",
    "the apothecary diaries": "The Apothecary Diaries",
    "kage no jitsuryokusha ni naritakute": "The Eminence in Shadow",
    "eminence in shadow": "The Eminence in Shadow",
    "danmachi": "Is It Wrong to Try to Pick Up Girls in a Dungeon?",
    "is it wrong to try to pick up girls in a dungeon": "Is It Wrong to Try to Pick Up Girls in a Dungeon?",
    "pokemon": "Pokémon",
    "digimon": "Digimon Adventure",
    "sailor moon": "Sailor Moon",
    "cardcaptor sakura": "Cardcaptor Sakura",
    "inuyasha": "InuYasha",
    "yu yu hakusho": "Yu Yu Hakusho",
    "fullmetal alchemist": "Fullmetal Alchemist: Brotherhood",
    "fullmetal alchemist brotherhood": "Fullmetal Alchemist: Brotherhood",
    "fairy tail": "FAIRY TAIL",
    "magi": "Magi: The Labyrinth of Magic",
    "noragami": "Noragami",
    "sword art online": "Sword Art Online",
    "sao": "Sword Art Online",
    "oregairu": "My Teen Romantic Comedy SNAFU",
    "my teen romantic comedy snafu": "My Teen Romantic Comedy SNAFU",
    "rascal does not dream": "Rascal Does Not Dream of Bunny Girl Senpai",
    "bunny girl senpai": "Rascal Does Not Dream of Bunny Girl Senpai",
    "toradora": "Toradora!",
    "your lie in april": "Your Lie in April",
    "anohana": "Anohana: The Flower We Saw That Day",
    "clannad": "Clannad",
    "violet evergarden": "Violet Evergarden",
    "your name": "Your Name.",
    "weathering with you": "Weathering With You",
    "a silent voice": "A Silent Voice",
    "spirited away": "Spirited Away",
    "my neighbor totoro": "My Neighbor Totoro",
    "totoro": "My Neighbor Totoro",
    "howls moving castle": "Howl's Moving Castle",
    "howl moving castle": "Howl's Moving Castle",
    "princess mononoke": "Princess Mononoke",
    "kiki delivery service": "Kiki's Delivery Service",
    "dragon ball z": "Dragon Ball Z",
    "dragon ball super": "Dragon Ball Super",
    "dragon ball daima": "Dragon Ball DAIMA",
    "dragon ball gt": "Dragon Ball GT",
    "boruto": "Boruto: Naruto Next Generations",
    "boruto naruto next generations": "Boruto: Naruto Next Generations",
    "naruto shippuden": "Naruto: Shippuden",
    "samurai champloo": "Samurai Champloo",
    "cowboy bebop": "Cowboy Bebop",
    "trigun": "Trigun",
    "evangelion": "Neon Genesis Evangelion",
    "neon genesis evangelion": "Neon Genesis Evangelion",
    "code geass": "Code Geass",
    "steins gate": "Steins;Gate",
    "erased": "ERASED",
    "parasyte": "Parasyte -the maxim-",
    "tokyo ghoul": "Tokyo Ghoul",
    "tokyo ghoulre": "Tokyo Ghoul:re",
    "akame ga kill": "Akame ga Kill!",
    "assassination classroom": "Assassination Classroom",
    "food wars": "Food Wars!",
    "shokugeki no soma": "Food Wars! Shokugeki no Soma",
    "the seven deadly sins": "The Seven Deadly Sins",
    "seven deadly sins": "The Seven Deadly Sins",
    "record of ragnarok": "Record of Ragnarok",
    "baki": "Baki",
    "baki hanma": "Baki Hanma",
    "kengan ashura": "Kengan Ashura",
    "moriarty the patriot": "Moriarty the Patriot",
    "bungou stray dogs": "Bungo Stray Dogs",
    "bungo stray dogs": "Bungo Stray Dogs",
    "durara": "Durarara!!",
    "durarara": "Durarara!!",
    "seraph of the end": "Seraph of the End",
    "goblin slayer": "Goblin Slayer",
    "overlord": "Overlord",
    "konosuba": "KONOSUBA -An Explosion on This Wonderful World!",
    "konosuba an explosion": "KONOSUBA -An Explosion on This Wonderful World!",
    "the rising of the shield hero": "The Rising of the Shield Hero",
    "shield hero": "The Rising of the Shield Hero",
    "arifureta": "Arifureta: From Commonplace to World's Strongest",
    "saga of tanya the evil": "Saga of Tanya the Evil",
    "tanya the evil": "Saga of Tanya the Evil",
    "no game no life": "No Game No Life",
    "log horizon": "Log Horizon",
    "the devil is a part timer": "The Devil Is a Part-Timer!",
    "devil is a part timer": "The Devil Is a Part-Timer!",
    "the ancient magus bride": "The Ancient Magus' Bride",
    "ancient magus bride": "The Ancient Magus' Bride",
    "to your eternity": "To Your Eternity",
    "the dangers in my heart": "The Dangers in My Heart",
    "insomniacs after school": "Insomniacs After School",
    "horimiya": "Horimiya",
    "skip and loafer": "Skip and Loafer",
    "my dress up darling": "My Dress-Up Darling",
    "dress up darling": "My Dress-Up Darling",
    "rent a girlfriend": "Rent-a-Girlfriend",
    "kakegurui": "Kakegurui",
    "tomodachi game": "Tomodachi Game",
    "summer time rendering": "Summertime Rendering",
    "odd taxi": "ODDTAXI",
    "pluto": "PLUTO",
    "cyberpunk edgerunners": "Cyberpunk: Edgerunners",
    "arcane": "Arcane",
    "kpop demon hunters": "KPop Demon Hunters",
    "pokemon indigo league": "Pokémon",
    "gintama": "Gintama",
    "hellsing": "Hellsing",
    "hellsing ultimate": "Hellsing Ultimate",
    "fate zero": "Fate/Zero",
    "fate stay night": "Fate/stay night",
    "fate stay night unlimited blade works": "Fate/stay night: Unlimited Blade Works",
    "fate grand order": "Fate/Grand Order",
    "monogatari": "Monogatari",
    "bakemonogatari": "Bakemonogatari",
    "made in abyss": "Made in Abyss",
    "86 eighty six": "86 EIGHTY-SIX",
    "eighty six": "86 EIGHTY-SIX",
    "bocchi the rock": "Bocchi the Rock!",
    "k on": "K-ON!",
    "love live": "Love Live! School Idol Project",
    "sound euphonium": "Sound! Euphonium",
    "a place further than the universe": "A Place Further than the Universe",
    "odd taxi": "ODDTAXI",
    "sonny boy": "Sonny Boy",
    "ranking of kings": "Ranking of Kings",
    "toilet bound hanako kun": "Toilet-bound Hanako-kun",
    "hanako kun": "Toilet-bound Hanako-kun",
    "akudama drive": "Akudama Drive",
    "wonder egg priority": "Wonder Egg Priority",
    "lycoris recoil": "Lycoris Recoil",
    "call of the night": "Call of the Night",
    "summertime render": "Summertime Rendering",
    "summertime rendering": "Summertime Rendering",
    "heavenly delusion": "Heavenly Delusion",
    "tengoku daimakyo": "Heavenly Delusion",
    "delicious in dungeon": "Delicious in Dungeon",
    "delicious in dungeon": "Delicious in Dungeon",
    "the weakest tamer began a journey to pick up trash": "The Weakest Tamer Began a Journey to Pick Up Trash",
    "faraway paladin": "The Faraway Paladin",
    "ranking of kings treasure chest": "Ranking of Kings: The Treasure Chest of Courage",
    "sword of the demon hunter": "Sword of the Demon Hunter",
    "wind breaker": "WIND BREAKER",
    "undead unluck": "Undead Unluck",
    "undead unlock": "Undead Unluck",
    "shangri la frontier": "Shangri-La Frontier",
    "shangri la frontier": "Shangri-La Frontier",
    "the wrong way to use healing magic": "The Wrong Way to Use Healing Magic",
    "the unwanted undead adventurer": "The Unwanted Undead Adventurer",
    "solo leveling": "Solo Leveling",
    "tower of god": "Tower of God",
    "god of high school": "The God of High School",
    "noblesse": "Noblesse",
    "lookism": "Lookism",
    "viral hit": "Viral Hit",
    "wind breaker anime": "WIND BREAKER",
    "hell paradise": "Hell's Paradise",
    "jigokuraku": "Hell's Paradise",
    "undead unluck": "Undead Unluck",
    "zom 100": "Zom 100: Bucket List of the Dead",
    "zom100": "Zom 100: Bucket List of the Dead",
    "drifters": "Drifters",
    "golden kamuy": "Golden Kamuy",
    "kingdom": "Kingdom",
    "world trigger": "World Trigger",
    "fire force": "Fire Force",
    "soul eater": "Soul Eater",
    "soul eater not": "Soul Eater NOT!",
    "blue giant": "BLUE GIANT",
    "mashle": "MASHLE: MAGIC AND MUSCLES",
    "mashle magic and muscles": "MASHLE: MAGIC AND MUSCLES",
    "shy": "SHY",
    "the duke of death and his maid": "The Duke of Death and His Maid",
    "my happy marriage": "My Happy Marriage",
    "7th time loop": "7th Time Loop: The Villainess Enjoys a Carefree Life Married to Her Worst Enemy!",
    "7th time loop": "7th Time Loop: The Villainess Enjoys a Carefree Life Married to Her Worst Enemy!",
    "villainess level 99": "Villainess Level 99",
    "why raeliana ended up at the duke mansion": "Why Raeliana Ended Up at the Duke's Mansion",
    "raeliana": "Why Raeliana Ended Up at the Duke's Mansion",
    "my next life as a villainess": "My Next Life as a Villainess",
    "ascendance of a bookworm": "Ascendance of a Bookworm",
    "the saint's magic power is omnipotent": "The Saint's Magic Power is Omnipotent",
    "saints magic power": "The Saint's Magic Power is Omnipotent",
    "snow white with the red hair": "Snow White with the Red Hair",
    "fruits basket": "Fruits Basket",
    "maid sama": "Maid Sama!",
    "kaichou wa maid sama": "Maid Sama!",
    "monthly girls nozaki kun": "Monthly Girls' Nozaki-kun",
    "wotakoi": "Wotakoi: Love Is Hard for Otaku",
    "love is hard for otaku": "Wotakoi: Love Is Hard for Otaku",
    "golden time": "Golden Time",
    "blue spring ride": "Ao Haru Ride",
    "ao haru ride": "Ao Haru Ride",
    "orange": "Orange",
    "relife": "ReLIFE",
    "nana": "NANA",
    "beck mongolian chop squad": "Beck: Mongolian Chop Squad",
    "initial d": "Initial D",
    "mf ghost": "MF GHOST",
    "haibane renmei": "Haibane Renmei",
    "serial experiments lain": "Serial Experiments Lain",
    "texhnolyze": "Texhnolyze",
    "black lagoon": "Black Lagoon",
    "darker than black": "Darker than Black",
    "psycho pass": "Psycho-Pass",
    "ghost in the shell": "Ghost in the Shell",
    "akira": "Akira",
    "perfect blue": "Perfect Blue",
    "paprika": "Paprika",
    "redline": "Redline",
    "promare": "PROMARE",
    "gurren lagann": "Gurren Lagann",
    "kill la kill": "Kill la Kill",
    "darling in the franxx": "DARLING in the FRANXX",
    "little witch academia": "Little Witch Academia",
    "cyborg 009": "Cyborg 009",
    "astro boy": "Astro Boy",
    "doraemon": "Doraemon",
    "crayon shin chan": "Crayon Shin-chan",
    "detective conan": "Detective Conan",
    "case closed": "Case Closed",
    "bakugan": "Bakugan Battle Brawlers",
    "inazuma eleven": "Inazuma Eleven",
    "digimon adventure 02": "Digimon Adventure 02",
    "yu gi oh": "Yu-Gi-Oh!",
    "yu gi oh duel monsters": "Yu-Gi-Oh! Duel Monsters",
    "beyblade": "Beyblade",
    "shaman king": "Shaman King",
    "zatch bell": "Zatch Bell!",
    "pokemon horizons": "Pokémon Horizons",
}

def _compact(n): return re.sub(r"[^a-z0-9]", "", normalizar_texto(n))

def _edit_distance(a,b):
    a=str(a); b=str(b)
    if a==b: return 0
    if not a: return len(b)
    if not b: return len(a)
    prev=list(range(len(b)+1))
    for i,ca in enumerate(a,1):
        cur=[i]
        for j,cb in enumerate(b,1):
            cur.append(min(cur[-1]+1, prev[j]+1, prev[j-1]+(ca!=cb)))
        prev=cur
    return prev[-1]

def _fuzzy_alias_match(text):
    """Recupera títulos com OCR quebrado, palavras invertidas ou pequenos erros.
    Em empates, prefere o alias mais específico/longo.
    """
    n=normalizar_texto(text)
    if not n: return None
    toks=n.split()
    if not toks: return None
    best=None  # (score, specificity, official)
    def consider(score, alias, official, minimum=80):
        if score < minimum: return
        spec=len(_compact(alias))
        if best is None or score>best[0]+1e-9 or (abs(score-best[0])<=2 and spec>best[1]):
            return (score,spec,official)
        return None
    def token_match_score(a,b):
        a=_compact(a); b=_compact(b)
        if a==b: return 100.0
        d=_edit_distance(a,b); m=max(len(a),len(b),1)
        if d==1 and m<=4: return 80.0
        if d==1 and m<=8: return 90.0
        return float(_fuzz_ratio(a,b))
    for alias,official in TITLE_ALIASES.items():
        at=normalizar_texto(alias).split()
        if not at: continue
        if len(at)==1 and len(at[0])<5: continue
        if len(at)==len(toks) and len(at)>=2:
            remaining=list(at); scores=[]
            for qt in toks:
                if not remaining: break
                vals=[token_match_score(qt,a) for a in remaining]
                j=max(range(len(remaining)), key=lambda i: vals[i])
                scores.append(vals[j]); remaining.pop(j)
            if len(scores)==len(at):
                avg=sum(scores)/len(scores); floor=min(scores)
                cand=consider(avg,alias,official,88 if floor>=76 else 999)
                if cand is not None: best=cand
                if avg>=82 and floor>=72 and any(len(w)>=5 and w in toks for w in at):
                    cand=consider(avg,alias,official,82)
                    if cand is not None: best=cand
        span_len=len(at)
        if len(at)==1 and len(toks)>1:
            continue
        for i in range(max(1, len(toks)-span_len+1)):
            window=toks[i:i+span_len]
            if len(window)!=span_len: continue
            wa=' '.join(window); aa=' '.join(at)
            sc=float(_fuzz_ratio(_compact(wa),_compact(aa)))
            distinctive=sum(1 for w in at if len(w)>=5 and w in window)
            if sc>=88 or (len(at)>=2 and sc>=78 and distinctive>=1):
                cand=consider(sc,alias,official,78 if distinctive else 88)
                if cand is not None: best=cand
        if len(at)==len(toks)+1 and at[-1].isdigit() and len(toks)>=2:
            matched=all(any(token_match_score(q,a)>=86 for a in at) for q in toks)
            if matched:
                cand=consider(91,alias,official,91)
                if cand is not None: best=cand
        if len(at)==1 and len(toks)==1 and len(at[0])>=5:
            sc=token_match_score(toks[0],at[0])
            if sc>=80:
                cand=consider(sc,alias,official,80)
                if cand is not None: best=cand
    return best[2] if best else None

def canonical_title_from_text(text):
    n=normalizar_texto(text); compact=_compact(n)
    if "cime dos mundos" in n:
        return None
    # Primeiro: igualdade exata da frase ou versão compactada.
    alias_items=sorted(TITLE_ALIASES.items(), key=lambda kv: len(_compact(kv[0])), reverse=True)
    for alias,official in alias_items:
        a=normalizar_texto(alias)
        if n == a or compact == _compact(a):
            return official
    # Depois: fuzzy forte, antes do fallback por substring. Isso impede
    # "Naruto Shippude" de cair em "Naruto" só porque "naruto" está contido.
    fuzzy=_fuzzy_alias_match(text)
    if fuzzy:
        return fuzzy
    # Em seguida, aliases contidos, ordenados do mais específico para o mais curto.
    for alias,official in alias_items:
        a=normalizar_texto(alias)
        if len(a.split())>=2 and (a in n or _compact(a) in compact):
            return official
    # Terceiro: combinações muito características de uma franquia.
    if "swordsmith" in n or ("swordsmith" in compact and "village" in compact):
        return "Demon Slayer: Kimetsu no Yaiba"
    if "demon" in n and ("layer" in n or "slayer" in n or "kimetsu" in n):
        return "Demon Slayer: Kimetsu no Yaiba"
    words=set(n.split())
    if "tsue" in words and "to" in words: return "Wistoria: Wand and Sword"
    if "wistoria" in words: return "Wistoria: Wand and Sword"
    return None

def infer_season_from_title_text(title_text, category="Anime"):
    """Resolve temporada/arco somente quando houver pista textual confiável."""
    n=normalizar_texto(title_text)
    compact=_compact(n)
    # Explicit season markers.
    patterns=(
        (r"\b(?:season|temporada|s)\s*0*(\d{1,2})\b", None),
        (r"\b0*(\d{1,2})(?:st|nd|rd|th)\s+season\b", None),
        (r"\b(?:season|temporada)\s+([a-z]+)\b", None),
    )
    ordinal_words={"first":1,"second":2,"third":3,"fourth":4,"fifth":5,"primeira":1,"segunda":2,"terceira":3,"quarta":4,"quinta":5}
    for pat,_ in patterns:
        m=re.search(pat,n,re.I)
        if m:
            raw=m.group(1).lower()
            try: sn=int(raw); return {"season":max(1,sn),"season_title":f"Season {max(1,sn)}","arc":f"Season {max(1,sn)}","verified_hint":True}
            except Exception:
                if raw in ordinal_words:
                    sn=ordinal_words[raw]; return {"season":sn,"season_title":f"Season {sn}","arc":f"Season {sn}","verified_hint":True}

    if "fire force" in n or "enen no shouboutai" in n:
        # Common official naming cues for this franchise. Only explicit text triggers them.
        if any(k in n for k in ("third season","3rd season","season 3","season3","s3","3a temporada","3 temporada","terceira temporada")):
            return {"season":3,"season_title":"Fire Force Season 3","arc":"Fire Force Season 3","verified_hint":True}
        if any(k in n for k in ("second season","2nd season","season 2","season2","s2","2a temporada","2 temporada","segunda temporada")):
            return {"season":2,"season_title":"Fire Force Season 2","arc":"Fire Force Season 2","verified_hint":True}
        if any(k in n for k in ("first season","1st season","season 1","season1","s1","1a temporada","1 temporada","primeira temporada")):
            return {"season":1,"season_title":"Fire Force Season 1","arc":"Fire Force Season 1","verified_hint":True}

    if ("demon slayer" in n or "kimetsu no yaiba" in n or "swordsmith" in n or "swordsmithvil" in compact or "jordsmith vil" in n or "jordsmithvil" in compact or "swordsmith vil" in n or "ordsmith vil" in n):
        if ("swordsmith village" in n or "swordsmith vil" in n or "ordsmith vil" in n or "jordsmith vil" in n or "jordsmithvil" in compact or "swordsmithvil" in compact or "ordsmithvil" in compact):
            return {"season":3,"season_title":"Swordsmith Village Arc","arc":"Swordsmith Village Arc","verified_hint":True}
        if "entertainment district" in n:
            return {"season":2,"season_title":"Entertainment District Arc","arc":"Entertainment District Arc","verified_hint":True}
        if "mugen train" in n:
            return {"season":2,"season_title":"Mugen Train Arc","arc":"Mugen Train Arc","verified_hint":True}
        if "final selection" in n or "infinity castle" in n:
            return {"season":1 if "final selection" in n else None,"season_title":"Infinity Castle","arc":"Infinity Castle","verified_hint":False}
    return {"season":None,"season_title":"","arc":"","verified_hint":False}

def _extract_known_title(text):
    flat=re.sub(r"\s+"," ",text or "")
    canonical=canonical_title_from_text(flat)
    return [canonical] if canonical else []

def translate_synopsis(text):
    text=limpar_linha(text)
    if not text or len(text)<8: return text
    key='synopsis_pt:'+normalizar_texto(text)
    cached=cache_get(key)
    if cached: return cached.get('text',text) if isinstance(cached,dict) else text
    try:
        r=requests.get('https://api.mymemory.translated.net/get',params={'q':text[:4800],'langpair':'en|pt-BR'},timeout=5,headers={'User-Agent':USER_AGENT})
        data=r.json() if r.ok else {}
        translated=((data.get('responseData') or {}).get('translatedText') or '').strip()
        if translated and translated.lower()!=text.lower(): cache_put(key,{'text':translated}); return translated
    except Exception as e: print('Tradução da sinopse indisponível:',e)
    return text

def clean_anime_candidates(text):
    if not text: return []
    raw=" ".join(limpar_linha(x) for x in str(text).splitlines())
    candidates=[]
    alias=canonical_title_from_text(raw)
    if alias: candidates.append(alias)
    patterns=[r"Tsue\s+to\s+Tsurugi\s+no\s+Wistoria",r"Wistoria",r"Jujutsu\s+Kaisen",r"One\s+Piece",r"Solo\s+Leveling",r"Attack\s+on\s+Titan",r"Demon\s+Slayer",r"Naruto",r"Bleach",r"Black\s+Clover",r"Fire\s+Force",r"Enen\s+no\s+Shouboutai"]
    for p in patterns:
        for m in re.findall(p,raw,re.I):
            if m and normalizar_texto(m) not in {normalizar_texto(x) for x in candidates}: candidates.append(m)
    for line in str(text).splitlines():
        line=limpar_linha(line)
        if not line or parece_lixo(line): continue
        parts=re.split(r"\s{2,}|\||•|·|—|–",line)
        for part in parts:
            part=limpar_linha(part); words=normalizar_texto(part).split()
            if (5<=len(part)<=70 and 2<=len(words)<=8 and
                not parece_lixo(part) and not _looks_like_sentence(part) and
                not any(w.isdigit() for w in words)):
                candidates.append(part)
    result=[]; seen=set()
    def rank(x):
        w=normalizar_texto(x).split(); pts=0
        pts+=sum(20 for p in w if p in TITLE_SIGNAL_WORDS)
        pts+=15 if 2<=len(w)<=6 else 0
        pts-=35 if any(v in w for v in {"apareceu","aparecer","clique","veja","confira","assista","resultado","curtir","curtido"}) else 0
        pts-=30 if any(v in w for v in {"comentarios","biblioteca","configuracoes","estatisticas","adicionar"}) else 0
        pts-=20 if len(w)>=7 else 0
        pts-=_context_penalty(w)
        return pts
    for c in sorted(candidates,key=rank,reverse=True):
        k=normalizar_texto(c)
        if k and k not in seen: seen.add(k); result.append(c)
    return result[:8]

# ============================================================
# PESQUISA ONLINE + CACHE + FALLBACK
# ============================================================

def _cache_key(q): return normalizar_texto(q)

def cache_get(q):
    k=_cache_key(q)
    try:
        c=db(); row=c.execute("SELECT data,created_at FROM anime_cache WHERE query=?",(k,)).fetchone(); c.close()
        if row and time.time()-float(row["created_at"])<CACHE_TTL_SECONDS: return json.loads(row["data"])
    except Exception: pass
    return None

def cache_put(q,data):
    k=_cache_key(q)
    if not k: return
    try:
        c=db(); c.execute("CREATE TABLE IF NOT EXISTS anime_cache(query TEXT PRIMARY KEY,data TEXT NOT NULL,created_at REAL NOT NULL)"); c.execute("INSERT OR REPLACE INTO anime_cache VALUES(?,?,?)",(k,json.dumps(data,ensure_ascii=False),time.time())); c.commit(); c.close()
    except Exception as e: print("Cache erro:",e)

def kitsu_search(query):
    """Busca anime no Kitsu. Não exige chave e funciona como terceiro fallback."""
    query=str(query or "").strip()
    if not query: return []
    try:
        r=requests.get(KITSU_URL+"/anime", params={"filter[text]":query,"page[limit]":10}, headers={"User-Agent":USER_AGENT,"Accept":"application/vnd.api+json"}, timeout=KITSU_TIMEOUT)
        if not r.ok: return []
        out=[]
        for row in (r.json().get("data") or []):
            a=row.get("attributes") or {}; titles=a.get("titles") or {}
            title=titles.get("canonical") or a.get("canonicalTitle") or ""
            if not title: continue
            cover=((a.get("posterImage") or {}).get("large") or (a.get("posterImage") or {}).get("original") or (a.get("posterImage") or {}).get("medium") or "")
            start=a.get("startDate") or ""
            year=int(start[:4]) if str(start)[:4].isdigit() else None
            out.append({"id":row.get("id"),"title":title,"title_english":titles.get("en") or titles.get("en_jp") or "","title_japanese":titles.get("ja_jp") or "","episodes":a.get("episodeCount"),"status":a.get("status") or "","score":(float(a.get("averageRating"))/10 if a.get("averageRating") not in (None,"") else None),"year":year,"type":a.get("showType") or "TV","url":"https://kitsu.io/anime/"+str(row.get("id")),"cover":cover,"images":{"kitsu":a.get("posterImage") or {}},"season":None,"aired":{"from":start},"duration":(str(a.get("episodeLength"))+" min") if a.get("episodeLength") else "","synopsis":a.get("synopsis") or "","synopsis_pt":translate_synopsis(a.get("synopsis") or ""),"genres":[],"category":"Anime","source":"Kitsu"})
        return out
    except Exception as e:
        print("Kitsu indisponível:",e); return []

def tmdb_search(query, media_type="multi"):
    """Busca filmes/séries no TMDB quando o usuário configurou um token."""
    if not TMDB_TOKEN or not query: return []
    try:
        r=requests.get("https://api.themoviedb.org/3/search/"+media_type, params={"query":query,"language":"pt-BR","include_adult":"false","page":1}, headers={"Authorization":"Bearer "+TMDB_TOKEN,"accept":"application/json","User-Agent":USER_AGENT}, timeout=8)
        if not r.ok: return []
        out=[]
        for a in (r.json().get("results") or [])[:10]:
            mt=media_type
            title=a.get("title") or a.get("name") or a.get("original_title") or a.get("original_name") or ""
            if not title: continue
            date=a.get("release_date") or a.get("first_air_date") or ""
            year=int(date[:4]) if str(date)[:4].isdigit() else None
            cover=("https://image.tmdb.org/t/p/w780"+str(a.get("poster_path"))) if a.get("poster_path") else ""
            out.append({"id":"tmdb:"+str(a.get("id")),"title":title,"title_english":a.get("original_title") or a.get("original_name") or "","title_japanese":"","episodes":None,"status":"","score":(float(a.get("vote_average")) if a.get("vote_average") is not None else None),"year":year,"type":"Filme" if mt=="movie" else "Série","url":"https://www.themoviedb.org/"+("movie/" if mt=="movie" else "tv/")+str(a.get("id")),"cover":cover,"images":{},"season":None,"aired":{},"duration":"","synopsis":a.get("overview") or "","synopsis_pt":a.get("overview") or "","genres":[],"category":"Filme" if mt=="movie" else "Série","source":"TMDB"})
        return out
    except Exception as e:
        print("TMDB indisponível:",e); return []

def _merge_search_results(*groups):
    merged=[]; seen=set()
    for group in groups:
        for item in group or []:
            key=(normalizar_texto(item.get("title") or item.get("name") or ""), item.get("source") or "")
            if not key[0] or key in seen: continue
            seen.add(key); merged.append(dict(item))
    return merged

def smart_search_title(title, aliases=None, category_hint="", diagnostics=None, fast_mode=False):
    """Motor de descoberta: várias bases independentes + IA web só quando necessário."""
    title=str(title or "").strip(); aliases=[str(x).strip() for x in (aliases or []) if str(x).strip()]
    if not title: return [], None
    trace=diagnostics if diagnostics is not None else []
    queries=[]
    for q in [title]+aliases:
        for v in _search_variants(q):
            if normalizar_texto(v) not in {normalizar_texto(x) for x in queries}: queries.append(v)
    queries=queries[:3 if fast_mode else 5]
    all_results=[]
    for q in queries[:1 if fast_mode else 3]:
        # Caminho rápido: prioriza Jikan porque normalmente já traz capa, episódios,
        # status e ano em uma única chamada. Outras fontes entram apenas como fallback.
        arr=[]
        cached=cache_get(q)
        if cached is not None:
            arr=cached
            trace.append({"source":"Jikan","query":q,"status":"cache_hit","count":len(arr)})
        else:
            data=jikan_request("/anime",{"q":q,"limit":5 if fast_mode else 10})
            if data:
                for a in data.get("data",[]):
                    imgs=a.get("images") or {}; jpg=imgs.get("jpg") or {}; webp=imgs.get("webp") or {}; cover=(jpg.get("large_image_url") or webp.get("large_image_url") or jpg.get("image_url") or webp.get("image_url"))
                    arr.append({"id":a.get("mal_id"),"title":a.get("title"),"title_english":a.get("title_english"),"title_japanese":a.get("title_japanese"),"episodes":a.get("episodes"),"status":a.get("status"),"score":a.get("score"),"year":a.get("year"),"type":a.get("type"),"url":a.get("url"),"cover":cover,"images":imgs,"season":a.get("season"),"aired":a.get("aired"),"duration":a.get("duration"),"synopsis":a.get("synopsis"),"synopsis_pt":((a.get("synopsis") or "") if fast_mode else translate_synopsis(a.get("synopsis") or "")),"genres":[g.get("name") for g in (a.get("genres") or [])],"category":"Anime","source":"Jikan"})
                cache_put(q,arr)
            trace.append({"source":"Jikan","query":q,"status":"result" if arr else "no_result","count":len(arr)})
        all_results.extend(arr)
        if fast_mode and not arr:
            local_item={"id":"local:"+normalizar_texto(title).replace(' ','-'),"title":title,"title_english":"","title_japanese":"","episodes":None,"status":"","score":None,"year":None,"type":"TV","cover":"","images":{},"season":None,"aired":{},"duration":"","synopsis":"","synopsis_pt":"","genres":[],"category":category_hint or "Anime","source":"Reconhecimento local"}
            trace.append({"source":"Reconhecimento local","query":q,"status":"instant_fallback","count":1})
            all_results.append(local_item)
    # No caminho rápido para identificação de anime, não bloqueie o usuário esperando
    # TMDB/TVMaze; capas/dados de Jikan/AniList são suficientes para o primeiro resultado.
    cat=normalizar_texto(category_hint)
    if cat in {"filme","serie","desenho",""} and not fast_mode:
        for q in queries[:2]:
            for mt in ("movie","tv"):
                arr=tmdb_search(q,mt); trace.append({"source":"TMDB","query":q,"status":"result" if arr else ("not_configured" if not TMDB_TOKEN else "no_result"),"count":len(arr)})
                all_results.extend(arr)
            arr=tvmaze_search(q); trace.append({"source":"TVMaze","query":q,"status":"result" if arr else "no_result","count":len(arr)}); all_results.extend(arr)
    # Ranking global, independente da ordem da fonte.
    ranked=[]; seen_titles=set()
    for item in all_results:
        score=_title_match_score(title,item)
        for a in aliases: score=max(score,_title_match_score(a,item))
        source=item.get("source") or ""
        bonus={"AniList":8,"Jikan":7,"Kitsu":6,"TMDB":6,"TVMaze":4}.get(source,0)
        score+=bonus
        item["_match_score"]=round(score,2)
        key=(normalizar_texto(item.get("title") or ""),normalizar_texto(item.get("title_english") or ""),item.get("source"))
        if key in seen_titles: continue
        seen_titles.add(key)
        if score>=SEARCH_MIN_CONFIDENCE: ranked.append(item)
    ranked.sort(key=lambda x:(x.get("_match_score",0),bool(x.get("episodes")),x.get("score") or 0),reverse=True)
    return ranked[:12], (ranked[0] if ranked else None)

def jikan_request(endpoint,params=None,tentativas=1):
    try:
        r=requests.get(JIKAN_URL+endpoint,params=params,headers={"User-Agent":USER_AGENT,"Accept":"application/json"},timeout=JIKAN_TIMEOUT)
        if r.status_code>=400:
            print(f"Jikan {r.status_code}: consulta encerrada sem repetir")
            return None
        return r.json()
    except Exception as e:
        print("Jikan indisponível:",e); return None

def anilist_search(name, translate=True):
    q='''query ($search:String){ Page(perPage:8){ media(search:$search,type:ANIME){ id title{romaji english native} genres episodes status averageScore format season seasonYear duration description siteUrl coverImage{large} startDate{year month day} endDate{year month day} } } }'''
    try:
        r=requests.post(ANILIST_URL,json={"query":q,"variables":{"search":name}},headers={"Content-Type":"application/json","User-Agent":USER_AGENT},timeout=ANILIST_TIMEOUT)
        if not r.ok:return []
        out=[]
        for a in r.json().get("data",{}).get("Page",{}).get("media",[]):
            t=a.get("title") or {}; cover=(a.get("coverImage") or {}).get("large")
            out.append({"id":a.get("id"),"title":t.get("romaji") or t.get("english") or t.get("native"),"title_english":t.get("english"),"title_japanese":t.get("native"),"episodes":a.get("episodes"),"status":a.get("status"),"score":(a.get("averageScore")/10 if a.get("averageScore") else None),"year":a.get("seasonYear") or (a.get("startDate") or {}).get("year"),"type":a.get("format"),"season":a.get("season"),"duration":(str(a.get("duration"))+" min por episódio") if a.get("duration") else "","url":a.get("siteUrl"),"cover":cover,"images":{"jpg":{"large_image_url":cover,"image_url":cover}} if cover else {},"synopsis":re.sub(r"<[^>]+>","",a.get("description") or ""),"synopsis_pt":(translate_synopsis(re.sub(r"<[^>]+>","",a.get("description") or "")) if translate else re.sub(r"<[^>]+>","",a.get("description") or "")),"genres":a.get("genres") or [],"category":"Anime","source":"AniList"})
        return out
    except Exception as e: print("AniList indisponível:",e); return []

# Informações de próximas temporadas verificadas externamente.
# Wistoria Season 3 foi oficialmente anunciada em 28/06/2026,
# mas a data de estreia ainda não foi anunciada.
KNOWN_UPCOMING = {
    "wistoria": {
        "season": 3,
        "title": "Wistoria: Wand and Sword",
        "status": "Produção confirmada",
        "release_date": None,
        "release_text": "Data de estreia ainda não anunciada",
        "announcement_date": "28/06/2026",
        "source_url": "https://wistoria-anime.com/news/610/"
    }
}

def upcoming_info(query):
    nq=normalizar_texto(query)
    for key, info in KNOWN_UPCOMING.items():
        if key in nq or key in normalizar_texto(info["title"]):
            return info
    return None



SEARCH_STOPWORDS = {
    "a","o","as","os","um","uma","uns","umas","e",
    "de","da","do","das","dos","em","no","na","nos","nas",
    "por","para","com","que","se","the"
}


def _fold_text(text):
    text = str(text or "")
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c))


def _title_tokens(text):
    return [t for t in re.findall(r"[a-z0-9]+", _fold_text(normalizar_texto(text))) if t]


def _search_variants(query):
    q = limpar_linha(str(query or ""))
    # OCR costuma colar palavras que originalmente estavam separadas (DragonBall, JujutsuKaisen).
    camel = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", q)
    if camel != q:
        q = camel
    if not q:
        return []
    variants = []
    def add(v):
        v = limpar_linha(v)
        if not v:
            return
        key = normalizar_texto(v)
        if key and key not in {normalizar_texto(x) for x in variants}:
            variants.append(v)

    add(q)
    # Remove lixo no começo/fim e palavras funcionais isoladas que o OCR costuma capturar.
    toks = q.split()
    while toks and normalizar_texto(toks[0]) in SEARCH_STOPWORDS:
        toks.pop(0)
    while len(toks) > 1 and normalizar_texto(toks[-1]) in SEARCH_STOPWORDS:
        toks.pop()
    if toks:
        add(" ".join(toks))
    # Para OCR truncado de duas ou três palavras, tenta o núcleo esquerdo.
    if len(toks) >= 2:
        add(" ".join(toks[:2]))
        # OCR às vezes inverte a ordem de duas palavras grandes.
        if len(toks) == 2:
            add(" ".join(reversed(toks)))
    if len(toks) >= 3:
        add(" ".join(toks[:3]))
        # Logos e capas podem fazer o OCR trocar a ordem de palavras adjacentes.
        # Testamos trocas locais sem gerar permutações explosivas.
        for i in range(min(len(toks) - 1, 6)):
            swapped=list(toks)
            swapped[i], swapped[i+1] = swapped[i+1], swapped[i]
            add(" ".join(swapped))
        # Duas rotações comuns quando o OCR junta a linha em ordem estranha.
        add(" ".join(toks[1:] + toks[:1]))
        add(" ".join(toks[-1:] + toks[:-1]))
    return variants[:10]


def _title_match_score(query, item):
    qn = normalizar_texto(query)
    qf = _fold_text(qn)
    qt = _title_tokens(query)
    if not qt:
        return -100

    aliases = []
    for key in ("title", "title_english", "title_japanese", "name"):
        val = item.get(key)
        if val:
            aliases.append(str(val))
    if not aliases:
        return -100

    best = -100
    qcompact = re.sub(r"[^a-z0-9]", "", qf)
    for alias in aliases:
        an = normalizar_texto(alias)
        af = _fold_text(an)
        at = _title_tokens(alias)
        acompact = re.sub(r"[^a-z0-9]", "", af)
        if not at:
            continue
        score = 0.0

        if qn == an or qf == af:
            score = 100.0
        elif acompact == qcompact:
            score = 98.0
        elif len(at) <= len(qt) and at and all(t in qt for t in at):
            # O título pode estar correto mesmo quando o OCR acrescentou lixo:
            # "Sword Art Online ia AS My" -> "Sword Art Online".
            pos=[]; start=0
            for tok in at:
                try: i=qt.index(tok,start); pos.append(i); start=i+1
                except ValueError: pos=[]; break
            if pos and pos == list(range(pos[0], pos[0]+len(at))):
                score = 92.0
            else:
                score = 84.0
        elif qn in an or qf in af:
            score = 82.0
        elif len(qcompact) >= 4 and acompact.startswith(qcompact):
            score = 78.0
        else:
            common = set(qt) & set(at)
            overlap = len(common) / max(1, len(set(qt)))
            ordered = all(t in at for t in qt)
            score = 28.0 + overlap * 48.0 + (10 if ordered else 0)

        # Similaridade de edição ajuda bastante em OCR quebrado.
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, qcompact, acompact).ratio()
        score = max(score, ratio * 78.0)
        if ratio >= 0.92:
            score = max(score, 88.0)
        elif ratio >= 0.84:
            score = max(score, 80.0)

        # Consulta de uma palavra precisa bater no início de algum token;
        # isso evita "Arte" virar "A Teacher" ou "A Confession" por acidente.
        if len(qt) == 1:
            token = qt[0]
            if qcompact == acompact:
                score = max(score, 98.0)
            elif token in at:
                score = max(score, 96.0)
            elif any(t.startswith(token) for t in at):
                score = max(score, 74.0)
            elif ratio >= 0.84:
                score = max(score, 82.0)
            elif token not in " ".join(at):
                score -= 25
        # Penaliza candidatos que acrescentam palavras estranhas quando a consulta
        # já parece um título curto e preciso.
        if len(qt) <= 2 and len(at) >= 4 and not (qn in an or qf in af):
            score -= 8
        best = max(best, score)
    return round(best, 2)


def _decorate_and_filter(results, query, category=None):
    clean = []
    seen = set()
    for item in results or []:
        if not item:
            continue
        score = _title_match_score(query, item)
        item["_match_score"] = score
        if category and not item.get("category"):
            item["category"] = category
        key = (item.get("id"), normalizar_texto(item.get("title") or item.get("name") or ""), item.get("source"))
        if key in seen:
            continue
        seen.add(key)
        # Não deixar busca fuzzy genérica trazer "qualquer coisa".
        if score >= 48 or score >= 70 and len(_title_tokens(query)) == 1:
            clean.append(item)
    clean.sort(key=lambda x: (x.get("_match_score",0), bool(x.get("episodes")), x.get("score") or 0), reverse=True)
    return clean


def tvmaze_search(query):
    try:
        r = requests.get("https://api.tvmaze.com/search/shows", params={"q":query}, timeout=7, headers={"User-Agent":USER_AGENT})
        if not r.ok:
            return []
        out=[]
        for row in r.json()[:8]:
            show=row.get("show") or {}
            image=show.get("image") or {}
            out.append({
                "id": show.get("id"),
                "title": show.get("name"),
                "title_english": show.get("name"),
                "title_japanese": None,
                "episodes": None,
                "status": show.get("status") or "",
                "score": show.get("rating",{}).get("average"),
                "year": (show.get("premiered") or "")[:4] or None,
                "type": "SERIES",
                "url": show.get("url"),
                "cover": image.get("original") or image.get("medium") or "",
                "images": {"tvmaze": image},
                "season": None,
                "aired": {},
                "duration": f"{show.get('runtime')} min" if show.get('runtime') else "",
                "synopsis": re.sub(r"<[^>]+>","",show.get("summary") or ""),
                "synopsis_pt": translate_synopsis(re.sub(r"<[^>]+>","",show.get("summary") or "")),
                "genres": show.get("genres") or [],
                "category": "Série",
                "source": "TVMaze"
            })
        return out
    except Exception as e:
        print("TVMaze indisponível:", e)
        return []


def wikidata_search(query):
    try:
        r=requests.get("https://www.wikidata.org/w/api.php", params={
            "action":"wbsearchentities","search":query,"language":"en","uselang":"en","format":"json","limit":8
        }, timeout=7, headers={"User-Agent":USER_AGENT})
        if not r.ok:
            return []
        out=[]
        for item in r.json().get("search",[]):
            label=item.get("label") or ""
            desc=item.get("description") or ""
            # Só usa como fallback de descoberta, sem inventar episódios ou nota.
            out.append({
                "id": item.get("id"),
                "title": label,
                "title_english": label,
                "title_japanese": None,
                "episodes": None,
                "status": "",
                "score": None,
                "year": None,
                "type": "DISCOVERY",
                "url": f"https://www.wikidata.org/wiki/{item.get('id')}" if item.get("id") else "",
                "cover": "",
                "images": {},
                "season": None,
                "aired": {},
                "duration": "",
                "synopsis": desc,
                "synopsis_pt": translate_synopsis(desc),
                "genres": [],
                "category": "Descoberta",
                "source": "Wikidata"
            })
        return out
    except Exception as e:
        print("Wikidata indisponível:",e)
        return []



def _trace_entry(trace, source, query, outcome, count=0, detail=""):
    if trace is None:
        return
    trace.append({
        "source": str(source),
        "query": str(query),
        "outcome": str(outcome),
        "count": int(count or 0),
        "detail": str(detail or "")[:240]
    })


def search_anime(name, diagnostics=None):
    name=str(name).strip()
    if not name:
        return []
    trace=diagnostics if diagnostics is not None else []
    canonical=canonical_title_from_text(name) or name
    variants=_search_variants(canonical)

    # Aliases seguros para títulos conhecidos.
    if canonical == "Fire Force":
        variants += ["Fire Force","Enen no Shouboutai"]
    elif canonical == "Tsue to Tsurugi no Wistoria":
        variants += ["Tsue to Tsurugi no Wistoria","Wistoria: Wand and Sword","Wistoria"]
    variants=list(dict.fromkeys(variants))[:3]

    merged=[]; seen=set()
    def collect(items, source=None):
        added=0
        for item in items or []:
            item=dict(item)
            if source and not item.get("source"):
                item["source"]=source
            key=(item.get("id"),normalizar_texto(item.get("title") or item.get("name") or ""),item.get("source"))
            if key in seen:
                continue
            seen.add(key); merged.append(item); added+=1
        return added

    for q in variants:
        cached=cache_get(q)
        if cached is not None:
            added=collect(cached)
            _trace_entry(trace,"Cache/Jikan",q,"cache_hit",added)
        else:
            data=jikan_request("/anime",{"q":q,"limit":10})
            arr=[]
            if data:
                for a in data.get("data",[]):
                    imgs=a.get("images") or {}; jpg=imgs.get("jpg") or {}; webp=imgs.get("webp") or {}
                    cover=(jpg.get("large_image_url") or webp.get("large_image_url") or jpg.get("image_url") or webp.get("image_url"))
                    arr.append({"id":a.get("mal_id"),"title":a.get("title"),"title_english":a.get("title_english"),"title_japanese":a.get("title_japanese"),"episodes":a.get("episodes"),"status":a.get("status"),"score":a.get("score"),"year":a.get("year"),"type":a.get("type"),"url":a.get("url"),"cover":cover,"images":imgs,"season":a.get("season"),"aired":a.get("aired"),"duration":a.get("duration"),"synopsis":a.get("synopsis"),"synopsis_pt":translate_synopsis(a.get("synopsis") or ""),"genres":[g.get("name") for g in (a.get("genres") or [])],"category":"Anime","source":"Jikan"})
            cache_put(q,arr); added=collect(arr)
            _trace_entry(trace,"Jikan",q,"result" if added else "no_result",added)
        if _decorate_and_filter(merged,q,"Anime"):
            break

    ranked=[]; seen_rank=set()
    for item in merged:
        scores=[_title_match_score(q,item) for q in variants]
        best_score=max(scores) if scores else -100
        item["_match_score"]=best_score
        key=(item.get("id"),normalizar_texto(item.get("title") or item.get("name") or ""),item.get("source"))
        if key in seen_rank or best_score < 48:
            continue
        seen_rank.add(key); ranked.append(item)
    ranked.sort(key=lambda x:(x.get("_match_score",0),bool(x.get("episodes")),x.get("score") or 0),reverse=True)

    if not ranked:
        for q in variants[:2]:
            cached=cache_get("anilist:"+q)
            if cached is not None:
                arr=cached; _trace_entry(trace,"Cache/AniList",q,"cache_hit",len(arr))
            else:
                arr=anilist_search(q); cache_put("anilist:"+q,arr)
                _trace_entry(trace,"AniList",q,"result" if arr else "no_result",len(arr))
            for item in arr:
                item["category"]="Anime"; item["source"]="AniList"
            collect(arr)
            ranked=_decorate_and_filter(merged,canonical,"Anime")
            if ranked: break

    if not ranked:
        for q in variants[:2]:
            arr=tvmaze_search(q)
            _trace_entry(trace,"TVMaze",q,"result" if arr else "no_result",len(arr))
            ranked=_decorate_and_filter(arr,canonical,"Série")
            if ranked:
                collect(ranked)
                break

    if not ranked:
        for q in variants[:2]:
            arr=wikidata_search(q)
            _trace_entry(trace,"Wikidata",q,"result" if arr else "no_result",len(arr))
            ranked=_decorate_and_filter(arr,canonical,"Descoberta")
            if ranked:
                collect(ranked)
                break

    if ranked:
        best=ranked[0].get("_match_score",0)
        ranked=[x for x in ranked if x.get("_match_score",0)>=max(58,best-24)]
        return ranked[:10]

    if canonical in {"Fire Force","Tsue to Tsurugi no Wistoria"}:
        _trace_entry(trace,"Reconhecimento local",canonical,"known_local",1)
        known={
            "Fire Force":{"id":None,"title":"Fire Force","title_english":"Fire Force","episodes":None,"status":"UNKNOWN","score":None,"year":None,"type":"TV","season":None,"cover":"","genres":["Action","Fantasy","Supernatural"],"synopsis":"","synopsis_pt":"","category":"Anime","source":"Reconhecimento local"},
            "Tsue to Tsurugi no Wistoria":{"id":None,"title":"Tsue to Tsurugi no Wistoria","title_english":"Wistoria: Wand and Sword","episodes":None,"status":"UNKNOWN","score":None,"year":None,"type":"TV","season":None,"cover":"","genres":["Action","Adventure","Fantasy"],"synopsis":"","synopsis_pt":"","category":"Anime","source":"Reconhecimento local"}
        }
        return [known[canonical]]
    return []

# ============================================================
# INFORMAÇÕES COMPLETAS
# ============================================================

def anime_info(anime_id):
    """Retorna detalhes do cache antes de tentar uma nova chamada externa."""
    if not str(anime_id).isdigit():
        return {"error": "ID inválido."}

    cached = cache_get("id:" + str(anime_id))
    if cached:
        return cached

    data = jikan_request(f"/anime/{anime_id}")
    if not data:
        return {
            "error": "Detalhes temporariamente indisponíveis.",
            "temporary": True,
            "id": int(anime_id)
        }

    anime = data.get("data", {})
    resultado = {
        "id": anime.get("mal_id"),
        "title": anime.get("title"),
        "title_english": anime.get("title_english"),
        "title_japanese": anime.get("title_japanese"),
        "episodes": anime.get("episodes"),
        "status": anime.get("status"),
        "score": anime.get("score"),
        "type": anime.get("type"),
        "year": anime.get("year"),
        "season": anime.get("season"),
        "aired": anime.get("aired"),
        "duration": anime.get("duration"),
        "synopsis": anime.get("synopsis"),
        "synopsis_pt": translate_synopsis(anime.get("synopsis") or ""),
        "url": anime.get("url"),
        "images": anime.get("images"),
        "cover": ((anime.get("images") or {}).get("jpg") or {}).get("large_image_url") or (((anime.get("images") or {}).get("webp") or {}).get("large_image_url")) or (((anime.get("images") or {}).get("jpg") or {}).get("image_url")) or (((anime.get("images") or {}).get("webp") or {}).get("image_url")),
        "genres": [g.get("name") for g in anime.get("genres", [])],
        "studios": [s.get("name") for s in anime.get("studios", [])]
    }
    cache_put("id:" + str(anime_id), resultado)
    return resultado


# ============================================================
# HANDLER
# ============================================================


def image_capture_datetime(body):
    try:
        img=Image.open(BytesIO(body))
        exif=img.getexif()
        for tag in (36867, 306):
            val=exif.get(tag) if exif else None
            if val:
                text=str(val).strip()
                if re.match(r"^\d{4}:\d{2}:\d{2} \d{2}:\d{2}:\d{2}$",text):
                    return text.replace(":","-",2)[:10]
    except Exception:
        pass
    return ""

class Handler(
    BaseHTTPRequestHandler
):

    server_version = "CimeDosMundos/4.27 VISION ULTIMATE"


    def log_message(
        self,
        format,
        *args
    ):

        print(
            f"[{datetime.now().strftime('%H:%M:%S')}] "
            + format % args
        )


    # ========================================================
    # JSON
    # ========================================================

    def send_json(
        self,
        data,
        status=200
    ):

        raw = json.dumps(
            data,
            ensure_ascii=False
        ).encode(
            "utf-8"
        )

        self.send_response(
            status
        )

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Cache-Control",
            "no-cache"
        )

        self.send_header(
            "Content-Length",
            str(len(raw))
        )

        self.end_headers()

        self.wfile.write(
            raw
        )


    # ========================================================
    # ARQUIVOS
    # ========================================================

    def send_file(
        self,
        path,
        content_type
    ):
        """Serve arquivos estáticos com cache em memória, ETag e gzip.
        O conteúdo é recarregado somente quando mtime/tamanho mudam.
        Isso reduz I/O e tráfego em PCs e celulares na rede local.
        """
        try:
            import gzip
            import threading
            fp = os.path.abspath(path)
            st = os.stat(fp)
            key = (fp, int(st.st_mtime_ns), int(st.st_size))
            cache = globals().setdefault('_STATIC_SEND_CACHE', {})
            lock = globals().setdefault('_STATIC_SEND_CACHE_LOCK', threading.RLock())
            with lock:
                entry = cache.get(key)
                if entry is None:
                    with open(fp, 'rb') as f:
                        raw = f.read()
                    etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
                    gz = gzip.compress(raw, compresslevel=6, mtime=0) if len(raw) >= 1024 else b''
                    entry = {'raw': raw, 'gzip': gz, 'etag': etag, 'mtime': st.st_mtime}
                    # Mantém somente os últimos 12 recursos para não crescer sem limite.
                    cache[key] = entry
                    while len(cache) > 12:
                        cache.pop(next(iter(cache)))

            inm = self.headers.get('If-None-Match', '')
            if inm and inm == entry['etag']:
                self.send_response(304)
                self.send_header('ETag', entry['etag'])
                self.send_header('Cache-Control', 'public, max-age=31536000, immutable' if content_type in ('image/png','image/svg+xml') else 'no-cache, private')
                self.send_header('Last-Modified', formatdate(entry['mtime'], usegmt=True))
                self.send_header('Vary', 'Accept-Encoding')
                self.end_headers()
                return

            accept = (self.headers.get('Accept-Encoding') or '').lower()
            compressible = str(content_type).startswith(('text/','application/json','application/javascript','application/manifest+json','image/svg+xml'))
            use_gzip = bool(entry['gzip']) and compressible and 'gzip' in accept and 'Range' not in self.headers
            body = entry['gzip'] if use_gzip else entry['raw']

            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('ETag', entry['etag'])
            self.send_header('Last-Modified', formatdate(entry['mtime'], usegmt=True))
            self.send_header('Cache-Control', 'public, max-age=31536000, immutable' if content_type in ('image/png','image/svg+xml') else 'no-cache, private')
            self.send_header('Vary', 'Accept-Encoding')
            if use_gzip:
                self.send_header('Content-Encoding', 'gzip')
            self.end_headers()
            self.wfile.write(body)

        except FileNotFoundError:
            self.send_json({'error':'Arquivo não encontrado.'},404)
        except Exception as e:
            self.send_json({'error':str(e)},500)


    # ========================================================
    # GET
    # ========================================================

    def do_GET(self):

        parsed = urlparse(
            self.path
        )

        path = unquote(
            parsed.path
        )

        # ----------------------------------------------------
        # PÁGINA
        # ----------------------------------------------------

        if path == "/":

            return self.send_file(
                os.path.join(
                    BASE,
                    "index.html"
                ),
                "text/html; charset=utf-8"
            )

        # ----------------------------------------------------
        # STATUS
        # ----------------------------------------------------

        if path == "/api/vision/status":
            return self.send_json({"configured":vision_configured(),"vision_model":OPENAI_VISION_MODEL if vision_configured() else "","research_model":OPENAI_RESEARCH_MODEL if vision_configured() else "","provider":"OpenAI Vision + Web Search" if vision_configured() else "OCR local","cache":True})

        if path == "/api/status":

            return self.send_json({

                "online":
                    True,

                "name":
                    "Cime dos Mundos",

                "version":
                    "5.0 BETA SMART VISION",

                "ocr":
                    True,

                "jikan":
                    True,

                "database":
                    os.path.exists(DB),

                "time":
                    datetime.now().isoformat(
                        timespec="seconds"
                    )
            })

        # ----------------------------------------------------
        # BIBLIOTECA
        # ----------------------------------------------------

        if path == "/api/titles":

            return self.send_json(
                all_titles()
            )

        if path == "/api/stats":
            return self.send_json(library_stats())

        if path == "/api/watch-rate":
            return self.send_json(automatic_watch_rate())

        if path == "/api/recent":
            conn = db()
            rows = conn.execute("SELECT * FROM titles WHERE last_watched_at != '' ORDER BY last_watched_at DESC LIMIT 8").fetchall()
            conn.close()
            return self.send_json([dict(r) for r in rows])

        if path.startswith("/api/history/"):
            ident=path.split("/")[-1]
            if not ident.isdigit():
                return self.send_json({"error":"ID inválido."},400)
            return self.send_json(title_history(int(ident)))

        if path == "/api/backup":
            try:
                memory=io.BytesIO()
                with zipfile.ZipFile(memory, "w", zipfile.ZIP_DEFLATED) as z:
                    if os.path.exists(DB):
                        z.write(DB, "cime.db")
                    conn=db()
                    export={"version":"5.0 BETA SMART VISION","created_at":datetime.now().isoformat(timespec="seconds"),"titles":all_titles(),"watch_history":[dict(r) for r in conn.execute("SELECT * FROM watch_history ORDER BY watched_at DESC").fetchall()]}
                    conn.close()
                    z.writestr("library.json", json.dumps(export,ensure_ascii=False,indent=2))
                    if os.path.isdir(MEDIA):
                        for root,_,files in os.walk(MEDIA):
                            for filename in files:
                                full=os.path.join(root,filename)
                                arc=os.path.join("media",os.path.relpath(full,MEDIA))
                                z.write(full,arc)
                    readme="Cime dos Mundos 5.0 BETA SMART VISION - backup\nInclui cime.db e library.json.\nO arquivo original nao deve ser sobrescrito durante testes.\n"
                    z.writestr("LEIA-ME-BACKUP.txt",readme)
                raw=memory.getvalue()
                self.send_response(200)
                self.send_header("Content-Type","application/zip")
                self.send_header("Content-Disposition",'attachment; filename="CimeDosMundos_Backup_5.0 BETA SMART VISION.zip"')
                self.send_header("Content-Length",str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            except Exception as e:
                return self.send_json({"error":"Não foi possível gerar o backup: "+str(e)},500)

        if path == "/api/export":
            conn=db()
            payload={"version":"5.0 BETA SMART VISION","exported_at":datetime.now().isoformat(timespec="seconds"),"titles":all_titles()}
            payload["watch_history"]=[dict(r) for r in conn.execute("SELECT * FROM watch_history ORDER BY watched_at DESC").fetchall()]
            conn.close()
            return self.send_json(payload)

        # ----------------------------------------------------
        # BUSCA DE ANIME
        # ----------------------------------------------------

        if path == "/api/anime/search":
            query=parse_qs(parsed.query).get("q",[""])[0].strip()
            if not query:return self.send_json({"error":"Informe o título."},400)
            diagnostics=[]
            candidatos=clean_anime_candidates(query) or [query]
            principal=canonical_title_from_text(query) or candidatos[0]
            deep=parse_qs(parsed.query).get("deep",["0"])[0]=="1"
            results,best=smart_search_title(principal,candidatos[1:6],"",diagnostics,fast_mode=not deep)
            ai=None
            if deep and (not results or not best or best.get("_match_score",0)<82) and vision_configured():
                ai=openai_web_research(principal,candidatos[1:6],"")
                if ai.get("found"):
                    ai_item={"id":"ai:"+normalizar_texto(ai.get("canonical_title") or principal),"title":ai.get("canonical_title") or principal,"title_english":ai.get("title_english") or "","title_japanese":ai.get("title_japanese") or "","episodes":ai.get("episodes"),"status":ai.get("status") or "","score":ai.get("score"),"year":ai.get("year"),"type":ai.get("category") or "","synopsis":ai.get("synopsis_pt") or "","synopsis_pt":ai.get("synopsis_pt") or "","genres":ai.get("genres") or [],"cover":ai.get("cover_url") or "","season":ai.get("season") or "","sources":ai.get("sources") or [],"source":"OpenAI Web Search","_match_score":float(ai.get("confidence") or 0)*100+10}
                    results=sorted(_merge_search_results([ai_item],results),key=lambda x:x.get("_match_score",0),reverse=True)[:12]
            return self.send_json({"query":query,"detected_title":principal,"candidates":candidatos[:8],"attempts":[principal],"results":results[:8],"upcoming":upcoming_info(principal),"ai_research":ai,"diagnostics":{"attempted_sources":sorted(set(x.get("source") for x in diagnostics if x.get("source"))),"steps":diagnostics,"matched_source":(results[0].get("source") if results else None),"matched_score":(results[0].get("_match_score") if results else None),"outcome":"found" if results else "not_found"}})

        # ----------------------------------------------------
        # INFORMAÇÕES
        # ----------------------------------------------------

        if path.startswith(
            "/api/anime/"
        ):

            partes = path.split(
                "/"
            )

            if (
                len(partes) >= 4
                and partes[3].isdigit()
            ):

                resultado = anime_info(
                    partes[3]
                )

                if "error" in resultado:

                    return self.send_json(
                        resultado,
                        500
                    )

                return self.send_json(
                    resultado
                )

        # ----------------------------------------------------
        # MÍDIA
        # ----------------------------------------------------

        if path.startswith(
            "/media/"
        ):

            nome = os.path.basename(
                path
            )

            caminho = os.path.join(
                MEDIA,
                nome
            )

            # Garantir que o arquivo está dentro da pasta MEDIA
            caminho_real = os.path.realpath(
                caminho
            )

            media_real = os.path.realpath(
                MEDIA
            )

            if os.path.commonpath([caminho_real, media_real]) != media_real:

                return self.send_json(
                    {
                        "error":
                            "Acesso inválido."
                    },
                    403
                )

            if os.path.exists(
                caminho_real
            ):

                ext = os.path.splitext(
                    caminho_real
                )[1].lower()

                tipos = {

                    ".jpg":
                        "image/jpeg",

                    ".jpeg":
                        "image/jpeg",

                    ".png":
                        "image/png",

                    ".webp":
                        "image/webp"
                }

                return self.send_file(
                    caminho_real,
                    tipos.get(
                        ext,
                        "application/octet-stream"
                    )
                )

            return self.send_json(
                {
                    "error":
                        "Arquivo não encontrado."
                },
                404
            )

        return self.send_json(
            {
                "error":
                    "Rota não encontrada."
            },
            404
        )


    # ========================================================
    # LER BODY
    # ========================================================

    def read_body(self):

        try:

            length = int(
                self.headers.get(
                    "Content-Length",
                    "0"
                )
            )

        except Exception:

            length = 0

        if length > MAX_UPLOAD_SIZE:

            raise ValueError(
                "Arquivo muito grande."
            )

        return self.rfile.read(
            length
        )


    # ========================================================
    # POST
    # ========================================================

    def do_POST(self):

        parsed = urlparse(
            self.path
        )

        path = unquote(
            parsed.path
        )

        # ----------------------------------------------------
        # ADICIONAR
        # ----------------------------------------------------

        if path == "/api/titles":

            try:

                body = self.read_body()

                data = json.loads(
                    body.decode(
                        "utf-8"
                    )
                )

                item = add_title(
                    data
                )

                return self.send_json(
                    item,
                    201
                )

            except ValueError as e:

                return self.send_json(
                    {
                        "error":
                            str(e)
                    },
                    400
                )

            except Exception as e:

                return self.send_json(
                    {
                        "error":
                            str(e)
                    },
                    500
                )

        if path == "/api/import/history":
            try:
                data=json.loads(self.read_body().decode('utf-8') or '{}')
                return self.send_json(import_external_history(data.get('source'), data.get('entries') or []),201)
            except ValueError as e: return self.send_json({'error':str(e)},400)
            except Exception as e: return self.send_json({'error':str(e)},500)

        if path == "/api/titles/cover-photo":
            try:
                data=json.loads(self.read_body().decode('utf-8') or '{}'); tid=int(data.get('title_id')); out=attach_local_cover_photos(tid,data.get('photos') or [])
                return self.send_json(out or {'error':'Título não encontrado.'},200 if out else 404)
            except Exception as e: return self.send_json({'error':str(e)},400)

        if path == "/api/import/history/list":
            return self.send_json(imported_history_rows())

        if path == "/api/import":
            try:
                body=self.read_body(); data=json.loads(body.decode("utf-8"))
                imported=0
                for item in data.get("titles",[]):
                    try:
                        existing=find_existing_title(item.get("name",""), item.get("source_id",""))
                        if existing: continue
                        add_title(item); imported += 1
                    except Exception:
                        continue
                return self.send_json({"success":True,"imported":imported})
            except Exception as e:
                return self.send_json({"error":str(e)},400)

        # ----------------------------------------------------
        # IA + PESQUISA WEB
        # ----------------------------------------------------
        if path == "/api/ai/research":
            try:
                body=self.read_body(); data=json.loads(body.decode("utf-8")); q=str(data.get("q") or "").strip();
                if not q:return self.send_json({"error":"Informe o título."},400)
                result=openai_web_research(q,data.get("aliases") or [],data.get("category") or "")
                return self.send_json(result,200 if result.get("configured") else 503)
            except Exception as e:
                return self.send_json({"found":False,"error":str(e)},500)

        # ----------------------------------------------------
        # IA VISUAL
        # ----------------------------------------------------
        if path == "/api/vision/analyze":
            try:
                body=self.read_body()
                if not body: return self.send_json({"error":"Imagem vazia."},400)
                result=openai_vision_analyze(body,self.headers.get("X-OCR-Hint",""))
                return self.send_json(result,200 if result.get("configured") else 503)
            except Exception as e:
                return self.send_json({"configured":vision_configured(),"recognized":False,"error":str(e)},500)

        # ----------------------------------------------------
        # UPLOAD + OCR
        # ----------------------------------------------------

        if path == "/api/upload":

            try:

                body = self.read_body()

                if not body:

                    return self.send_json(
                        {
                            "error":
                                "Imagem vazia."
                        },
                        400
                    )

                filename = self.headers.get(
                    "X-Filename",
                    "imagem.jpg"
                )

                ext = os.path.splitext(
                    filename
                )[1].lower()

                extensoes = {
                    ".jpg",
                    ".jpeg",
                    ".png",
                    ".webp"
                }

                if ext not in extensoes:

                    ext = ".jpg"

                # ------------------------------------------------
                # Verificar se realmente é imagem
                # ------------------------------------------------

                try:

                    teste = Image.open(
                        BytesIO(body)
                    )

                    teste.verify()

                except Exception:

                    return self.send_json(
                        {
                            "error":
                                "O arquivo enviado não é uma imagem válida."
                        },
                        400
                    )

                # ------------------------------------------------
                # Nome aleatório
                # ------------------------------------------------

                nome = (
                    uuid.uuid4().hex
                    +
                    ext
                )

                caminho = os.path.join(
                    MEDIA,
                    nome
                )

                with open(
                    caminho,
                    "wb"
                ) as f:

                    f.write(
                        body
                    )

                # ------------------------------------------------
                # DESCOBERTA MULTICAMADA — FAST PATH
                # Visão e OCR-âncora começam em paralelo. OCR profundo só entra quando
                # a visão inicial não alcança confiança suficiente.
                # ------------------------------------------------
                t_pipeline=time.perf_counter()
                if vision_configured():
                    # Caminho turbo: visão primeiro. OCR local só é executado quando a visão não entrega
                    # uma identificação confiável, evitando trabalho e atraso desnecessários no celular.
                    vision=openai_vision_analyze(body, "", False)
                    anchor_text=""
                    if not (vision.get("recognized") and float(vision.get("confidence",0) or 0)>=0.86):
                        anchor_text=read_image_anchor_text(body)
                else:
                    vision={"configured":False,"recognized":False,"title_raw":"","aliases":[],"category":"","season":None,"season_title":"","arc":"","confidence":0}
                    anchor_text=read_image_anchor_text(body)

                vconf=float(vision.get("confidence",0) or 0) if isinstance(vision,dict) else 0.0
                fast_visual=bool(vision.get("recognized")) and vconf>=0.86
                deep_visual=False
                # Só pagamos o custo da análise profunda quando o caminho rápido não é confiável.
                need_deep = vision_configured() and (not fast_visual or not str(vision.get("title_raw") or vision.get("title") or "").strip())
                if need_deep:
                    deep_hint=" ".join(str(x) for x in [vision.get("title_raw") or "", *(vision.get("aliases") or []), anchor_text] if str(x).strip())
                    deep_vision=openai_vision_analyze(body, deep_hint[:1800], True)
                    if deep_vision.get("recognized") or float(deep_vision.get("confidence",0) or 0)>vconf:
                        vision=deep_vision
                        deep_visual=True
                        vconf=float(vision.get("confidence",0) or 0)

                fast_visual=bool(vision.get("recognized")) and float(vision.get("confidence",0) or 0)>=0.86
                anchor_known = canonical_title_from_text(anchor_text) or ""
                if fast_visual:
                    ocr_candidates=[x for x in [vision.get("title_raw") or vision.get("title") or "", *(vision.get("aliases") or []), anchor_known, anchor_text] if str(x).strip()]
                elif anchor_known:
                    # Caminho local turbo: se o OCR de âncora já encontrou um título
                    # conhecido, não gastamos ~5s numa bateria OCR profunda.
                    ocr_candidates=[anchor_known, anchor_text]
                else:
                    ocr_candidates=read_image_candidates(body)

                texto="\n".join(str(x) for x in ocr_candidates[:8])
                ocr_blob=" ".join(str(x) for x in ocr_candidates[:12])+" "+str(anchor_text or "")
                known_from_ocr=canonical_title_from_text(ocr_blob) or ""
                season_hint=infer_season_from_title_text(ocr_blob + ' ' + str(filename))
                if known_from_ocr and known_from_ocr not in ocr_candidates:
                    ocr_candidates.insert(0,known_from_ocr)

                candidatos = []
                vtitle = str(vision.get("title_raw") or vision.get("title") or "").strip()
                if vtitle:
                    candidatos.append(vtitle)
                candidatos.extend(str(a).strip() for a in (vision.get("aliases") or []) if str(a).strip())
                for alt in (vision.get("alternatives") or []):
                    if isinstance(alt,dict) and alt.get("title"):
                        candidatos.append(str(alt.get("title")).strip())
                if known_from_ocr:
                    candidatos.append(known_from_ocr)
                candidatos.extend(ocr_candidates)

                clean_candidates = []
                seen_c = set()
                for c in candidatos:
                    c = limpar_linha(c)
                    if not c:
                        continue
                    canonical_c = canonical_title_from_text(c)
                    value = canonical_c or c
                    key = normalizar_texto(value)
                    if not key or key in seen_c:
                        continue
                    if _is_ui_phrase(value) or _looks_like_sentence(value):
                        continue
                    seen_c.add(key)
                    clean_candidates.append(value)
                    if len(clean_candidates) >= 10:
                        break

                if vtitle and vision.get("confidence", 0) >= 0.85:
                    clean_candidates = [vtitle] + [
                        x for x in clean_candidates
                        if normalizar_texto(x) != normalizar_texto(vtitle)
                    ]

                candidatos = clean_candidates
                canonical = clean_candidates[0] if clean_candidates else ""

                search_trace = []
                results = []
                best = None
                research = None
                offline_identity = False

                if canonical:
                    aliases_for_search = [
                        x for x in clean_candidates[1:8]
                        if normalizar_texto(x) != normalizar_texto(canonical)
                    ]
                    _cat_hint = vision.get("category", "") if isinstance(vision, dict) else ""
                    if fast_visual and not _cat_hint:
                        _cat_hint = "Anime"
                    offline_identity = (not vision_configured() and bool(known_from_ocr) and normalizar_texto(canonical) == normalizar_texto(known_from_ocr))
                    if offline_identity:
                        results = [{"title":canonical,"type":"Anime","category":"Anime","source":"Repertório local","_match_score":96.0}]
                        best = results[0]
                        search_trace.append({"source":"Repertório local","query":canonical,"status":"instant_local_match","count":1})
                    else:
                        # Pesquisa normal + pesquisa dirigida pela temporada detectada.
                        # Isso evita que "Fire Force" caia sempre na T1 quando a imagem
                        # traz claramente S2/T3.
                        search_aliases = list(aliases_for_search)
                        hinted_season = season_hint.get('season') if isinstance(season_hint, dict) else None
                        if hinted_season:
                            search_aliases += [
                                f"{canonical} Season {hinted_season}",
                                f"{canonical} {hinted_season}th Season" if hinted_season>3 else f"{canonical} {hinted_season}{'nd' if hinted_season==2 else 'rd' if hinted_season==3 else 'st'} Season"
                            ]
                        results, best = smart_search_title(
                            canonical,
                            search_aliases,
                            _cat_hint,
                            search_trace,
                            fast_mode=False if hinted_season else bool(fast_visual and vision.get("confidence",0) >= 0.86)
                        )
                        if hinted_season and results:
                            season_hits=[]
                            for _r in results:
                                rs=_r.get('season')
                                title_norm=normalizar_texto(_r.get('title') or '')
                                q_norm=normalizar_texto(canonical)
                                if rs and str(rs).isdigit() and int(rs)==int(hinted_season):
                                    _r['_match_score']=float(_r.get('_match_score',0))+18
                                    season_hits.append(_r)
                                elif f"season {hinted_season}" in title_norm or f"{hinted_season} season" in title_norm:
                                    _r['_match_score']=float(_r.get('_match_score',0))+14
                                    season_hits.append(_r)
                            if season_hits:
                                results=sorted(results,key=lambda x:x.get('_match_score',0),reverse=True)
                                best=results[0]
                                search_trace.append({'stage':'season_targeted_search','query':f'{canonical} Season {hinted_season}','status':'matched','count':len(season_hits)})

                    # Se a primeira hipótese não fechar, consultamos até duas hipóteses
                    # secundárias em paralelo. Isso reduz falsos positivos sem tornar
                    # toda imagem lenta: só acontece quando há ambiguidade real.
                    best_score = float(best.get("_match_score", 0)) if best else 0
                    visual_title = str(vision.get("title_raw") or vision.get("title") or "").strip()
                    known_hit = known_from_ocr or ""
                    mismatch = bool(visual_title and best and _title_match_score(visual_title,best) < 82)
                    need_secondary = (not best or best_score < 84 or mismatch) and len(clean_candidates) > 1
                    if need_secondary:
                        import concurrent.futures
                        secondary = [x for x in clean_candidates[1:4] if normalizar_texto(x) != normalizar_texto(canonical)]
                        def _search_one(q):
                            local_trace=[]
                            rr,bb=smart_search_title(q, [], _cat_hint, local_trace, fast_mode=True)
                            return rr,bb,local_trace,q
                        try:
                            with concurrent.futures.ThreadPoolExecutor(max_workers=min(2,len(secondary))) as _sx:
                                outs=list(_sx.map(_search_one, secondary[:2]))
                            for rr,bb,tr,q in outs:
                                search_trace.append({"stage":"secondary_candidate","query":q,"status":"result" if rr else "no_result"})
                                search_trace.extend(tr)
                                if rr:
                                    results=_merge_search_results(results,rr)
                            results=sorted(results,key=lambda x:x.get("_match_score",0),reverse=True)[:12]
                            best=results[0] if results else best
                            best_score=float(best.get("_match_score",0)) if best else 0
                        except Exception:
                            pass

                    combined_verify = None
                    # Verificação multimodal só entra se ainda existir conflito.
                    # Preferimos confirmar do que adivinhar.
                    if vision_configured() and (not best or best_score < 84 or mismatch or (vision.get("confidence",0) or 0) < 0.86):
                        combined_verify = openai_vision_web_identify(body, texto[:1800])
                        if combined_verify.get("found"):
                            v_verified=combined_verify.get("canonical_title") or combined_verify.get("title") or canonical
                            canonical=v_verified
                            if combined_verify.get("season") is not None:
                                vision["season"]=combined_verify.get("season")
                            if combined_verify.get("season_title"):
                                vision["season_title"]=combined_verify.get("season_title")
                            if combined_verify.get("arc"):
                                vision["arc"]=combined_verify.get("arc")
                            ai_item={
                                "id":"ai-vision:"+normalizar_texto(v_verified),"title":v_verified,
                                "title_english":combined_verify.get("title_english") or "",
                                "title_japanese":combined_verify.get("title_japanese") or "",
                                "episodes":combined_verify.get("episodes"),"status":combined_verify.get("status") or "", "season_number":combined_verify.get("season"),
                                "score":combined_verify.get("score"),"year":combined_verify.get("year"),
                                "type":combined_verify.get("category") or "Anime","synopsis":combined_verify.get("synopsis_pt") or "",
                                "synopsis_pt":combined_verify.get("synopsis_pt") or "","genres":combined_verify.get("genres") or [],
                                "cover":combined_verify.get("cover_url") or "","season":combined_verify.get("season_title") or combined_verify.get("season") or "",
                                "url":(combined_verify.get("sources") or [{}])[0].get("url","") if combined_verify.get("sources") else "",
                                "sources":combined_verify.get("sources") or [],"source":"OpenAI Vision + Web",
                                "_match_score":float(combined_verify.get("confidence") or 0)*100+20
                            }
                            results=_merge_search_results([ai_item],results)
                            results=sorted(results,key=lambda x:x.get("_match_score",0),reverse=True)[:12]
                    else:
                        combined_verify = None
                    if (not results or best_score < 82) and vision_configured() and not offline_identity:
                        research = openai_web_research(
                            canonical,
                            aliases_for_search[:5],
                            vision.get("category", "") if isinstance(vision, dict) else ""
                        )
                        if research.get("found"):
                            ai_item = {
                                "id": "ai:" + normalizar_texto(research.get("canonical_title") or canonical),
                                "title": research.get("canonical_title") or canonical,
                                "title_english": research.get("title_english") or "",
                                "title_japanese": research.get("title_japanese") or "",
                                "episodes": research.get("episodes"),
                                "status": research.get("status") or "",
                                "score": research.get("score"),
                                "year": research.get("year"),
                                "type": research.get("category") or "",
                                "synopsis": research.get("synopsis_pt") or "",
                                "synopsis_pt": research.get("synopsis_pt") or "",
                                "genres": research.get("genres") or [],
                                "cover": research.get("cover_url") or "",
                                "season": research.get("season") or "", "season_number": research.get("season_number"),
                                "url": (research.get("sources") or [{}])[0].get("url", "") if research.get("sources") else "",
                                "sources": research.get("sources") or [],
                                "source": "OpenAI Web Search",
                                "_match_score": float(research.get("confidence") or 0) * 100 + 12
                            }
                            results = _merge_search_results([ai_item], results)
                            results = sorted(results, key=lambda x: x.get("_match_score", 0), reverse=True)[:12]

                final_title = (best or {}).get("title") or canonical
                vconf=float(vision.get("confidence",0) or 0) if isinstance(vision,dict) else 0
                combined_title=str((combined_verify or {}).get("canonical_title") or (combined_verify or {}).get("title") or "").strip()
                # Ordem de confiança: confirmação multimodal > correspondência de OCR/
                # fontes > visão isolada. Visão isolada só vence sem confirmação quando
                # a confiança é extremamente alta; assim o sistema prefere pedir confirmação
                # a retornar um anime errado.
                if combined_verify and combined_verify.get("found") and _valid_ai_title(combined_title):
                    final_title = combined_title
                elif known_from_ocr and best and _title_match_score(known_from_ocr,best) >= 84:
                    final_title = best.get("title") or known_from_ocr
                elif best and float(best.get("_match_score", 0)) >= 84:
                    final_title = best.get("title") or final_title
                elif vision.get("recognized") and vtitle and vconf >= 0.94:
                    final_title = vtitle
                else:
                    final_title = canonical or final_title
                vision_steps = [
                    {"stage":"vision_fast","status":"recognized" if fast_visual else ("candidate" if vision.get("title_raw") else "not_recognized"),
                     "mode":vision.get("vision_mode","fast"),"detail":vision.get("evidence") or ""},
                    {"stage":"vision_deep","status":"used" if deep_visual else "skipped",
                     "detail":"Executada apenas quando o caminho rápido não alcançou confiança suficiente."},
                    {"stage":"ocr_multicamada","status":"used" if ocr_candidates else "no_result",
                     "detail":"; ".join(ocr_candidates[:8])[:900]},
                    {"stage":"vision",
                     "status":"recognized" if vision.get("recognized") else
                               ("candidate" if vision.get("title_raw") else
                                ("error" if vision.get("error") else "not_recognized")),
                     "detail":vision.get("error") or vision.get("evidence") or "",
                     "alternatives":vision.get("alternatives") or []}
                ]
                vision_steps.extend(search_trace)
                if 'combined_verify' in locals() and combined_verify is not None:
                    vision_steps.append({"stage":"vision_web_verify","status":"found" if combined_verify.get("found") else ("error" if combined_verify.get("error") else "no_result"),"detail":combined_verify.get("evidence") or combined_verify.get("error") or ""})
                if research is not None:
                    vision_steps.append({
                        "stage":"openai_web_search",
                        "status":"found" if research.get("found") else ("error" if research.get("error") else "no_result"),
                        "detail":research.get("error") or (research.get("sources") or [])[:5]
                    })
                canonical = final_title or canonical
                # Confiança operacional: combina fonte visual, OCR e correspondência externa.
                # Prefer the matched external record as the official poster source.
                # The uploaded recognition image is never returned as a cover candidate.
                if best:
                    if best.get('cover') and isinstance(best.get('cover'), str):
                        vision['official_cover'] = best.get('cover')
                    if best.get('season') and not season_number:
                        try: season_number = int(best.get('season'))
                        except Exception: pass
                final_match = float((best or {}).get("_match_score",0) or 0)
                vconf=float(vision.get("confidence",0) or 0) if isinstance(vision,dict) else 0.0
                if offline_identity:
                    identity_confidence = 0.93
                elif combined_verify and combined_verify.get("found"):
                    identity_confidence = min(0.99, max(0.0, float(combined_verify.get("confidence",vconf) or vconf)))
                elif known_from_ocr and final_match >= 84:
                    identity_confidence = min(0.98, max(vconf, final_match/100))
                elif final_match >= 84:
                    identity_confidence = min(0.96, max(vconf, final_match/100))
                elif vconf >= 0.94:
                    identity_confidence = min(0.94, vconf)
                else:
                    identity_confidence = min(0.70, max(vconf, final_match/100))
                identity_verified = bool(offline_identity or (identity_confidence >= 0.86 and (best or combined_verify and combined_verify.get("found"))))
                season_number = vision.get("season") if isinstance(vision,dict) else None
                season_title = vision.get("season_title","") if isinstance(vision,dict) else ""
                arc_title = vision.get("arc","") if isinstance(vision,dict) else ""
                # Pesquisa externa pode resolver a temporada quando a imagem não traz marcador explícito.
                if season_number is None and isinstance(research,dict) and research.get("season_number"):
                    season_number = research.get("season_number")
                if not season_title and isinstance(research,dict) and research.get("season"):
                    season_title = str(research.get("season") or "")
                if season_number is None and season_hint.get("verified_hint"):
                    season_number = season_hint.get("season")
                if not season_title and season_hint.get("season_title"):
                    season_title = season_hint.get("season_title")
                if not arc_title and season_hint.get("arc"):
                    arc_title = season_hint.get("arc")

                elapsed_ms=int((time.perf_counter()-t_pipeline)*1000)
                captured_at = image_capture_datetime(body)
                return self.send_json({
                    "success": True,"path":"/media/"+nome,"captured_at":captured_at,"text":texto,"candidates":candidatos[:10],"canonical_title":canonical,"title":canonical,"recognized":bool(canonical),"identity_confidence":round(identity_confidence,3),"identity_verified":identity_verified,"season":season_number,"season_number":season_number,"season_title":season_title,"arc":arc_title,"cover":str((best or {}).get("cover") or vision.get("official_cover") or (research or {}).get("cover_url") or ""),"vision":vision,"research":research,
                    "results":results[:10],"timing":{"elapsed_ms":elapsed_ms,"seconds":round(elapsed_ms/1000,2),"fast_visual_path":fast_visual,"deep_visual_path":deep_visual,"target":"rápido primeiro; profundo só quando necessário"},"diagnostics":{"steps":vision_steps,"outcome":"found" if results else ("recognized" if canonical else "not_found"),"mode":"fast_parallel_vision_then_multisource" if vision_configured() else "ocr_then_multisource","sources_tried":sorted(set(x.get("source") for x in vision_steps if x.get("source")))}
                })

            except ValueError as e:

                return self.send_json(
                    {
                        "error":
                            str(e)
                    },
                    400
                )

            except Exception as e:

                print(
                    "Erro upload:",
                    e
                )

                return self.send_json(
                    {
                        "error":
                            str(e)
                    },
                    500
                )

        return self.send_json(
            {
                "error":
                    "Rota não encontrada."
            },
            404
        )


    # ========================================================
    # PUT
    # ========================================================

    def do_PUT(self):

        parsed = urlparse(
            self.path
        )

        path = unquote(
            parsed.path
        )

        if path.startswith(
            "/api/titles/"
        ):

            partes = path.split(
                "/"
            )

            if (
                len(partes) != 4
                or not partes[3].isdigit()
            ):

                return self.send_json(
                    {
                        "error":
                            "ID inválido."
                    },
                    400
                )

            title_id = int(
                partes[3]
            )

            try:

                body = self.read_body()

                data = json.loads(
                    body.decode(
                        "utf-8"
                    )
                )

                item = update_title(
                    title_id,
                    data
                )

                if not item:

                    return self.send_json(
                        {
                            "error":
                                "Título não encontrado."
                        },
                        404
                    )

                return self.send_json(
                    item
                )

            except Exception as e:

                return self.send_json(
                    {
                        "error":
                            str(e)
                    },
                    400
                )

        return self.send_json(
            {
                "error":
                    "Rota não encontrada."
            },
            404
        )


    # ========================================================
    # DELETE
    # ========================================================

    def do_DELETE(self):

        parsed = urlparse(
            self.path
        )

        path = unquote(
            parsed.path
        )

        if path.startswith(
            "/api/titles/"
        ):

            partes = path.split(
                "/"
            )

            if (
                len(partes) != 4
                or not partes[3].isdigit()
            ):

                return self.send_json(
                    {
                        "error":
                            "ID inválido."
                    },
                    400
                )

            title_id = int(
                partes[3]
            )

            sucesso = delete_title(
                title_id
            )

            if not sucesso:

                return self.send_json(
                    {
                        "error":
                            "Título não encontrado."
                    },
                    404
                )

            return self.send_json({

                "success":
                    True,

                "message":
                    "Título removido."
            })

        return self.send_json(
            {
                "error":
                    "Rota não encontrada."
            },
            404
        )


# ============================================================
# INICIAR SERVIDOR
# ============================================================

def iniciar():

    init_db()

    print()
    print(
        "=============================================="
    )
    print(
        "        🌌 CIME DOS MUNDOS 5.0 BETA SMART VISION"
    )
    print(
        "=============================================="
    )
    print(
        "📚 Biblioteca: ATIVADA"
    )
    print(
        "🗄️ SQLite: ATIVADO"
    )
    print(
        "👁️ OCR: ATIVADO"
    )
    print(
        "🇧🇷 Português: ATIVADO"
    )
    print(
        "🇺🇸 Inglês: ATIVADO"
    )
    print(
        "🔎 Pesquisa inteligente: ATIVADA"
    )
    print(
        "🌐 Jikan / MyAnimeList: ATIVADO"
    )
    print(
        "📤 Upload de imagens: ATIVADO"
    )
    print(
        "📱 Acesso pela rede local: ATIVADO"
    )
    print("📺 Progresso de episódios: ATIVADO")
    print("❤️ Favoritos: ATIVADO")
    print("📊 Estatísticas: ATIVADAS")
    print(
        "=============================================="
    )
    print(
        f"🌐 http://localhost:{PORT}"
    )
    print(
        f"🔌 Porta: {PORT}"
    )
    print(
        "=============================================="
    )
    print()

    servidor = ThreadingHTTPServer(
        (
            "0.0.0.0",
            PORT
        ),
        Handler
    )

    try:

        servidor.serve_forever()

    except KeyboardInterrupt:

        print()
        print(
            "Servidor encerrado."
        )

    finally:

        servidor.server_close()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    iniciar()
