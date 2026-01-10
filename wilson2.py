import streamlit as st
import google.generativeai as genai
import pandas as pd
import json
import time
import requests
import re
from urllib.parse import urljoin, urlparse
from tavily import TavilyClient

# --- 1. 頁面設定 ---
st.set_page_config(page_title="超級業務開發助手 (深度挖掘版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (自動追蹤聯絡頁面版)")
st.markdown("""
### 🚀 升級功能：深度挖掘 (Deep Crawl)
你猜對了！之前只爬首頁容易漏資料。
現在，如果首頁找不到 Email，程式會**自動尋找並點擊**「聯絡我們」或「Contact」頁面，把藏在內頁的資料挖出來！
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    
    if "GEMINI_API_KEY" in st.secrets:
        gemini_api_key = st.secrets["GEMINI_API_KEY"]
        st.success("✅ 已讀取 Gemini Key")
    else:
        gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")

    if "TAVILY_API_KEY" in st.secrets:
        tavily_api_key = st.secrets["TAVILY_API_KEY"]
        st.success("✅ 已讀取 Tavily Key")
    else:
        tavily_api_key = st.text_input("輸入 Tavily API Key", type="password")

    num_results = st.slider("搜尋數量", 3, 20, 5) 
    debug_mode = st.toggle("顯示後台處理過程", value=True)

# --- 3. 核心工具 ---

def get_root_url(url):
    """ 強制轉回首頁 """
    if not url: return ""
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    except:
        return url

def find_contact_link(markdown_text, root_url):
    """
    從 Jina 回傳的 Markdown 中尋找「聯絡我們」的連結
    格式通常是: [Link Text](URL)
    """
    # 尋找包含 "聯絡", "Contact", "About", "關於" 的連結
    links = re.findall(r'\[(.*?)\]\((.*?)\)', markdown_text)
    
    keywords = ["聯絡", "contact", "about", "關於", "support"]
    
    for text, link in links:
        for kw in keywords:
            if kw in text.lower():
                # 處理相對路徑 (例如 /contact.html 轉為 https://abc.com/contact.html)
                full_link = urljoin(root_url, link)
                return full_link, text
    return None, None

def fetch_content_smart(url, fallback_content=""):
    """ 
    智慧抓取流程：
    1. 抓首頁
    2. 如果首頁沒 Email，找 Contact 連結
    3. 抓 Contact 頁面
    """
    if fallback_content is None: fallback_content = ""
    
    combined_content = ""
    source_log = []

    # --- 步驟 1: 抓首頁 ---
    root_url = get_root_url(url)
    jina_url = f"https://r.jina.ai/{root_url}"
    
    try:
        resp = requests.get(jina_url, timeout=10)
        if resp.status_code == 200 and len(resp.text) > 200:
            homepage_text = resp.text
            combined_content += f"\n=== 首頁內容 ===\n{homepage_text[:15000]}"
            source_log.append("首頁")
            
            # --- 步驟 2: 檢查是否需要深度挖掘 ---
            # 如果首頁沒抓到 Email，嘗試找連結
            if "@" not in homepage_text:
                contact_link, link_text = find_contact_link(homepage_text, root_url)
                
                if contact_link:
                    source_log.append(f"追蹤內頁({link_text})")
                    # 抓取內頁
                    jina_contact_url = f"https://r.jina.ai/{contact_link}"
                    resp_inner = requests.get(jina_contact_url, timeout=10)
                    if resp_inner.status_code == 200:
                        combined_content += f"\n=== 內頁({link_text}) ===\n{resp_inner.text[:15000]}"
        else:
            # Jina 失敗，使用 Tavily 庫存
            if len(fallback_content) > 50:
                combined_content = fallback_content
                source_log.append("庫存")
                
    except:
        # 發生錯誤，退回使用庫存
        if len(fallback_content) > 50:
            combined_content = fallback_content
            source_log.append("庫存(救援)")

    return combined_content, " + ".join(source_log)

def regex_backup(text):
    """ 暴力掃描 """
    if not text: return [], []
    try:
        text_clean = " ".join(text.split())
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text_clean)
        phones = re.findall(r'(?:\(?0\d{1,2}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}', text_clean)
        valid_phones = [p for p in list(set(phones)) if len(re.sub(r'\D', '', p)) >= 8]
        return list(set(emails)), valid_phones
    except:
        return [], []

# --- 4. AI 分析函數 ---

def extract_contact_info(content, url, model):
    emails, phones = regex_backup(content)
    
    try:
        backup_info = f"Email: {emails[:3]}, 電話: {phones[:5]}"
        prompt = f"""
        你是一個資料提取機器人。請分析網頁內容找出聯絡方式。
        注意：內容可能包含首頁和聯絡我們內頁的資料。
        
        網址：{url}
        參考數據：{backup_info}

        網頁內容摘要：
        {content[:40000]} 
        
        請回傳 JSON：
        {{
            "公司名稱": "...", 
            "電話": "...", 
            "Email": "...",
            "傳真": "...",
            "網址": "{url}"
        }}
        """
        response = model.generate_content(prompt)
        txt = response.text.strip()
        
        if "```json" in txt:
            txt = txt.split("```json")[1].split("```")[0]
        elif "```" in txt:
            txt = txt.split("```")[0]
            
        data = json.loads(txt)

        if (not data.get("Email") or data.get("Email") == "None") and emails:
            data["Email"] = emails[0]
        if (not data.get("電話") or data.get("電話") == "None") and phones:
            data["電話"] = phones[0]

        return data

    except:
        return {
            "公司名稱": "ERROR", 
            "電話": phones[0] if phones else "", 
            "Email": emails[0] if emails else "", 
            "傳真": "",
            "網址": url
        }

# --- 5. 主程式 ---
keyword = st.text_input("🔍 請輸入搜尋關鍵字", value="廢水回收系統 公司")

if st.button("開始搜尋與分析"):
    if not gemini_api_key or not tavily_api_key:
        st.error("❌ 請輸入 API Key")
    else:
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        tavily = TavilyClient(api_key=tavily_api_key)
        
        status_box = st.status("🚀 啟動深度爬蟲...", expanded=True)
        results_list = []
        
        try:
            status_box.write(f"正在搜尋：{keyword}...")
            response = tavily.search(query=keyword, max_results=num_results, include_raw_content=True)
            search_results = response.get('results', [])
            
            if not search_results:
                status_box.error("找不到網址")
            else:
                progress_bar = st.progress(0)
                
                for i, item in enumerate(search_results):
                    try:
                        url = item.get('url', '無網址')
                        title = item.get('title', '無標題')
                        tavily_raw = item.get('raw_content') or ""
                        
                        status_box.write(f"({i+1}/{len(search_results)}) 分析：{title}")
                        
                        # --- 關鍵變更：使用新的 fetch 函數 ---
                        content, source_log = fetch_content_smart(url, fallback_content=tavily_raw)
                        
                        if debug_mode:
                            # 讓你知道程式有沒有跑去抓內頁
                            with st.expander(f"🔍 追蹤路徑: {source_log}"):
                                st.text(f"資料長度: {len(content)}")
                        
                        if len(content) > 50:
                            data = extract_contact_info(content, url, model)
                            
                            name = str(data.get("公司名稱", ""))
                            if name == "ERROR" or "失敗" in name or name == "None":
                                data["公司名稱"] = title
                            
                            results_list.append(data)
                        else:
                            pass
                            
                    except:
                        pass
                        
                    progress_bar.progress((i + 1) / len(search_results))
                    # 因為多爬一頁，禮貌性暫停稍微久一點點
                    time.sleep(1)

                status_box.update(label="🎉 深度搜集完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    cols = ["公司名稱", "電話", "Email", "傳真", "網址"]
                    
                    for c in cols:
                        if c not in df.columns: df[c] = ""
                    df = df[cols]

                    st.dataframe(df)
                    
                    excel_file = "leads_deep.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button("📥 下載 Excel 名單", f, file_name=f"{keyword}_深度名單.xlsx")

        except Exception as e:
            st.error(f"發生錯誤：{e}")