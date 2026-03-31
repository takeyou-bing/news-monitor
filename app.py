"""
네이버 뉴스 → 텔레그램 모니터링 봇 (웹 버전)
pip install streamlit requests schedule
"""

import re
import json
import time
import hashlib
import threading
import requests
import schedule
import streamlit as st
from datetime import datetime
from pathlib import Path

import urllib3
urllib3.disable_warnings()

# ─────────────────────────────────────────
# 설정
# ─────────────────────────────────────────
CONFIG_FILE = Path("config.json")
CACHE_FILE  = Path("sent_articles.json")

DEFAULT_CONFIG = {
    "naver_client_id":     "",
    "naver_client_secret": "",
    "telegram_bot_token":  "",
    "telegram_chat_id":    "",
    "keywords":            [],
    "display_count":       10,
    "interval_minutes":    10,
    "sort":                "date",
}

def load_config():
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def load_cache():
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()

def save_cache(sent):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(list(sent)[-5000:], f, ensure_ascii=False)

def article_id(link):
    return hashlib.md5(link.encode()).hexdigest()

# ─────────────────────────────────────────
# 네이버 / 텔레그램
# ─────────────────────────────────────────
def clean_html(text):
    return re.sub(r"<[^>]+>", "", text).replace("&amp;","&").replace("&lt;","<").replace("&gt;",">").replace("&quot;",'"')

def search_naver(keyword, cfg):
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id":     cfg["naver_client_id"],
        "X-Naver-Client-Secret": cfg["naver_client_secret"],
    }
    params = {"query": keyword, "display": cfg["display_count"], "start": 1, "sort": cfg["sort"]}
    res = requests.get(url, headers=headers, params=params, timeout=10, verify=False)
    res.raise_for_status()
    return res.json().get("items", [])

def send_telegram(message, cfg):
    url = f"https://api.telegram.org/bot{cfg['telegram_bot_token']}/sendMessage"
    payload = {"chat_id": cfg["telegram_chat_id"], "text": message, "parse_mode": "HTML", "disable_web_page_preview": False}
    res = requests.post(url, json=payload, timeout=10, verify=False)
    res.raise_for_status()

def format_message(keyword, article):
    title       = clean_html(article.get("title", ""))
    description = clean_html(article.get("description", ""))
    link        = article.get("link", "")
    pub_date    = article.get("pubDate", "")
    try:
        dt = datetime.strptime(pub_date, "%a, %d %b %Y %H:%M:%S %z")
        date_str = dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        date_str = pub_date
    return (
        f"🔔 <b>[{keyword}]</b> 새 뉴스\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📰 <b>{title}</b>\n\n"
        f"{description[:150]}{'...' if len(description) > 150 else ''}\n\n"
        f"🕐 {date_str}\n🔗 {link}"
    )

# ─────────────────────────────────────────
# 모니터링 로직
# ─────────────────────────────────────────
def monitor_once(cfg, log_list):
    sent  = load_cache()
    new_n = 0
    log_list.append(f"=== 검색 시작 ({datetime.now().strftime('%H:%M:%S')}) ===")
    for kw in cfg["keywords"]:
        try:
            articles = search_naver(kw, cfg)
            log_list.append(f"[{kw}] {len(articles)}건 검색")
            for art in articles:
                aid = article_id(art.get("link", ""))
                if aid in sent:
                    continue
                try:
                    send_telegram(format_message(kw, art), cfg)
                    sent.add(aid)
                    new_n += 1
                    log_list.append(f"  ✅ 전송: {clean_html(art.get('title',''))[:40]}")
                    time.sleep(0.3)
                except Exception as e:
                    log_list.append(f"  ❌ 전송 오류: {e}")
        except Exception as e:
            log_list.append(f"[{kw}] 검색 오류: {e}")
        time.sleep(0.5)
    save_cache(sent)
    log_list.append(f"=== 완료: 새 기사 {new_n}건 전송 ===")
    # 최근 200줄만 유지
    if len(log_list) > 200:
        del log_list[:len(log_list)-200]

def run_scheduler(cfg, log_list, stop_event):
    schedule.clear()
    schedule.every(cfg["interval_minutes"]).minutes.do(monitor_once, cfg, log_list)
    monitor_once(cfg, log_list)  # 즉시 1회 실행
    while not stop_event.is_set():
        schedule.run_pending()
        time.sleep(10)

# ─────────────────────────────────────────
# Streamlit 세션 상태 초기화
# ─────────────────────────────────────────
if "cfg" not in st.session_state:
    st.session_state.cfg = load_config()
if "running" not in st.session_state:
    st.session_state.running = False
if "log" not in st.session_state:
    st.session_state.log = []
if "stop_event" not in st.session_state:
    st.session_state.stop_event = None
if "thread" not in st.session_state:
    st.session_state.thread = None

cfg = st.session_state.cfg

# ─────────────────────────────────────────
# UI
# ─────────────────────────────────────────
st.set_page_config(page_title="뉴스 모니터링 봇", page_icon="📰", layout="wide")

st.markdown("""
<style>
    .stApp { background-color: #0f1117; }
    .block-container { padding-top: 1.5rem; }
    .status-running { color: #2ecc71; font-weight: bold; font-size: 1.1rem; }
    .status-stopped { color: #e74c3c; font-weight: bold; font-size: 1.1rem; }
    .log-box { background: #0d1117; color: #58a6ff; font-family: monospace;
               font-size: 0.8rem; padding: 12px; border-radius: 8px;
               height: 320px; overflow-y: auto; white-space: pre-wrap; }
    div[data-testid="stHorizontalBlock"] { align-items: center; }
</style>
""", unsafe_allow_html=True)

# 헤더
col_title, col_status = st.columns([3, 1])
with col_title:
    st.markdown("## 📰 뉴스 모니터링 봇")
with col_status:
    if st.session_state.running:
        st.markdown('<p class="status-running">🟢 실행 중</p>', unsafe_allow_html=True)
    else:
        st.markdown('<p class="status-stopped">⏹ 정지됨</p>', unsafe_allow_html=True)

st.divider()

# 탭
tab1, tab2, tab3 = st.tabs(["⚙ 설정", "🔑 API 키", "📋 로그"])

# ── 설정 탭 ──────────────────────────────
with tab1:
    st.subheader("검색 설정")

    col1, col2, col3 = st.columns(3)
    with col1:
        display_count = st.number_input("검색 건수 (최대 100)", min_value=1, max_value=100,
                                         value=cfg["display_count"])
    with col2:
        interval = st.number_input("검색 주기 (분)", min_value=1, max_value=1440,
                                    value=cfg["interval_minutes"])
    with col3:
        sort_opt = st.selectbox("정렬 방식",
                                ["date (최신순)", "sim (정확도순)"],
                                index=0 if cfg["sort"] == "date" else 1)

    st.divider()
    st.subheader("키워드 관리")

    kw_col1, kw_col2 = st.columns([4, 1])
    with kw_col1:
        new_kw = st.text_input("키워드 입력", placeholder="예: 빙그레 -광고  /  삼성전자 실적  /  빙그레|해태",
                                label_visibility="collapsed")
    with kw_col2:
        if st.button("➕ 추가", use_container_width=True):
            if new_kw.strip():
                if new_kw.strip() not in cfg["keywords"]:
                    cfg["keywords"].append(new_kw.strip())
                    st.success(f"'{new_kw.strip()}' 추가됨")
                    st.rerun()
                else:
                    st.warning("이미 등록된 키워드예요.")

    # 키워드 목록
    if cfg["keywords"]:
        st.markdown("**등록된 키워드**")
        for i, kw in enumerate(cfg["keywords"]):
            k1, k2 = st.columns([5, 1])
            with k1:
                st.markdown(f"` {kw} `")
            with k2:
                if st.button("삭제", key=f"del_{i}"):
                    cfg["keywords"].pop(i)
                    st.rerun()
    else:
        st.info("등록된 키워드가 없어요. 위에서 추가해 주세요.")

    st.divider()

    # 저장 + 실행
    btn1, btn2, _ = st.columns([1, 1, 3])
    with btn1:
        if st.button("💾 설정 저장", use_container_width=True):
            cfg["display_count"]    = display_count
            cfg["interval_minutes"] = interval
            cfg["sort"]             = "date" if "date" in sort_opt else "sim"
            save_config(cfg)
            st.success("저장됐어요!")

    with btn2:
        if not st.session_state.running:
            if st.button("▶ 모니터링 시작", use_container_width=True, type="primary"):
                if not cfg["naver_client_id"] or not cfg["telegram_bot_token"]:
                    st.error("API 키 탭에서 네이버/텔레그램 키를 먼저 입력해주세요.")
                elif not cfg["keywords"]:
                    st.error("키워드를 최소 1개 이상 등록해주세요.")
                else:
                    cfg["display_count"]    = display_count
                    cfg["interval_minutes"] = interval
                    cfg["sort"]             = "date" if "date" in sort_opt else "sim"
                    save_config(cfg)
                    stop_event = threading.Event()
                    t = threading.Thread(
                        target=run_scheduler,
                        args=(cfg, st.session_state.log, stop_event),
                        daemon=True
                    )
                    t.start()
                    st.session_state.running    = True
                    st.session_state.stop_event = stop_event
                    st.session_state.thread     = t
                    st.rerun()
        else:
            if st.button("⏹ 모니터링 중지", use_container_width=True):
                if st.session_state.stop_event:
                    st.session_state.stop_event.set()
                schedule.clear()
                st.session_state.running    = False
                st.session_state.stop_event = None
                st.rerun()

# ── API 키 탭 ─────────────────────────────
with tab2:
    st.subheader("네이버 API")
    naver_id     = st.text_input("Client ID",     value=cfg["naver_client_id"])
    naver_secret = st.text_input("Client Secret", value=cfg["naver_client_secret"], type="password")

    st.subheader("텔레그램")
    tg_token = st.text_input("Bot Token", value=cfg["telegram_bot_token"], type="password")
    tg_chat  = st.text_input("Chat ID",   value=cfg["telegram_chat_id"],
                              help="개인 채팅: 양수 숫자 / 그룹 채팅: - 로 시작하는 음수 숫자")

    col_save, col_test, _ = st.columns([1, 2, 3])
    with col_save:
        if st.button("💾 저장", use_container_width=True):
            cfg["naver_client_id"]     = naver_id.strip()
            cfg["naver_client_secret"] = naver_secret.strip()
            cfg["telegram_bot_token"]  = tg_token.strip()
            cfg["telegram_chat_id"]    = tg_chat.strip()
            save_config(cfg)
            st.success("API 키가 저장됐어요!")

    with col_test:
        if st.button("🔔 텔레그램 테스트 전송", use_container_width=True):
            test_cfg = {**cfg,
                        "naver_client_id":     naver_id.strip(),
                        "naver_client_secret": naver_secret.strip(),
                        "telegram_bot_token":  tg_token.strip(),
                        "telegram_chat_id":    tg_chat.strip()}
            try:
                send_telegram("✅ 테스트 메시지입니다. 뉴스봇 연결 성공!", test_cfg)
                st.success("텔레그램으로 테스트 메시지 전송 성공!")
            except Exception as e:
                st.error(f"전송 실패: {e}")

# ── 로그 탭 ──────────────────────────────
with tab3:
    col_refresh, col_clear, _ = st.columns([1, 1, 4])
    with col_refresh:
        if st.button("🔄 새로고침", use_container_width=True):
            st.rerun()
    with col_clear:
        if st.button("🗑 로그 지우기", use_container_width=True):
            st.session_state.log.clear()
            st.rerun()

    log_text = "\n".join(st.session_state.log[-100:]) if st.session_state.log else "로그가 없어요. 모니터링을 시작해 주세요."
    st.markdown(f'<div class="log-box">{log_text}</div>', unsafe_allow_html=True)

    if st.session_state.running:
        st.caption("⏱ 로그를 보려면 새로고침 버튼을 눌러주세요.")
