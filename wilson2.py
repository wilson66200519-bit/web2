import streamlit as st
import google.generativeai as genai
import pandas as pd
import json
import time
import requests
import re
from urllib.parse import urlparse
from tavily import TavilyClient

# --- 1. 頁面設定 ---
st.set_page_config(page_title="超級業務開發助手 (完美名稱版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (名稱自動修復版)")
st.markdown("""
### 🚀 功能完成！
現在即使 AI 暫時無法運作，程式也會：
1. **自動抓取** 網頁中的電話與 Email (Regex 技術)。
2. **自動填入** 搜尋到的公司標題 (不再顯示錯誤訊息)。
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
    debug_mode = st.toggle("顯示後台處理過程", value=False)

# --- 3. 核心工具 ---

def get_root_url(url):
    """ 強制轉回首頁 """
    if not url: return ""
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    except:
        return url

def fetch_content_smart(url, fallback_content=""):
    """ 智慧抓取流程 """
    if fallback_content is None:
        fallback_content = ""

    # 嘗試 1: Jina
    try:
        target_url = get_root_url(url)
        jina_url = f"https://r.jina.ai/{target_url}"
        resp = requests.get(jina_url, timeout=10)
        if resp.status_code == 200 and len(resp.text) > 200:
            return resp.text
    except:
        pass 
    
    # 嘗試 2: Tavily 庫存
    if len(fallback_content) > 50:
        return fallback_content
        
    return ""

def regex_backup(text):
    """ 暴力掃描 Email 和 電話 """
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
    # 預先掃描
    emails, phones = regex_backup(content)
    
    try:
        backup_info = f"Email: {emails[:3]}, 電話: {phones[:5]}"
        prompt = f"""
        你是一個資料提取機器人。請分析網頁內容找出聯絡方式。
        
        網址：{url}
        參考數據：{backup_info}

        網頁內容摘要：
        {content[:30000]} 
        
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

        # 強制補位機制
        if (not data.get("Email") or data.get("Email") == "None") and emails:
            data["Email"] = emails[0]
        if (not data.get("電話") or data.get("電話") == "None") and phones:
            data["電話"] = phones[0]

        return data

    except:
        # 當 AI 失敗時，回傳一個特殊的標記名稱 "ERROR"
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
        # 嘗試使用 flash 模型，如果不行也沒關係，我們有備案
        model = genai.GenerativeModel('gemini-1.5-flash')
        tavily = TavilyClient(api_key=tavily_api_key)
        
        status_box = st.status("🚀 正在努力搜集中...", expanded=True)
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
                        
                        content = fetch_content_smart(url, fallback_content=tavily_raw)
                        
                        if len(content) > 50:
                            data = extract_contact_info(content, url, model)
                            
                            # --- [關鍵修正] 只要名稱是 ERROR 或 失敗，就直接用標題取代 ---
                            name = str(data.get("公司名稱", ""))
                            if name == "ERROR" or "失敗" in name or name == "None":
                                data["公司名稱"] = title
                            
                            results_list.append(data)
                        else:
                            pass
                            
                    except:
                        pass
                        
                    progress_bar.progress((i + 1) / len(search_results))
                    time.sleep(0.5)

                status_box.update(label="🎉 搜集完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    cols = ["公司名稱", "電話", "Email", "傳真", "網址"]
                    
                    for c in cols:
                        if c not in df.columns: df[c] = ""
                    df = df[cols]

                    st.dataframe(df)
                    
                    excel_file = "leads_perfect.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button("📥 下載 Excel 名單", f, file_name=f"{keyword}_客戶名單.xlsx")

        except Exception as e:
            st.error(f"發生錯誤：{e}")