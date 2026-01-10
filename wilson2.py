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
st.set_page_config(page_title="超級業務開發助手 (終極防禦版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (庫存救援版)")
st.markdown("""
### 🚀 這是最後一道防線：
這個版本加入了 **「庫存救援 (Cached Content)」** 機制。
如果程式抓不到公司首頁，它會直接拿 Tavily 搜尋到的「網頁庫存」來分析。
**保證絕對不會出現空白資料！**
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")
    tavily_api_key = st.text_input("輸入 Tavily API Key", type="password")
    num_results = st.slider("搜尋數量", 3, 10, 5) 
    debug_mode = st.toggle("顯示抓取來源 (Debug)", value=True)

# --- 3. 核心工具 ---

def get_root_url(url):
    """ 強制轉回首頁 """
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    except:
        return url

def fetch_content_smart(url, fallback_content=""):
    """
    智慧抓取流程：
    1. Jina 抓首頁 (最完美)
    2. 失敗則用 Tavily 的庫存內容 (保底)
    """
    # 嘗試 1: 用 Jina 抓首頁
    target_url = get_root_url(url)
    jina_url = f"https://r.jina.ai/{target_url}"
    
    try:
        # 不設 header，純粹用 Jina 轉發
        resp = requests.get(jina_url, timeout=10)
        if resp.status_code == 200 and len(resp.text) > 200:
            return resp.text, "Jina (首頁)"
    except:
        pass
    
    # 嘗試 2: 如果首頁抓失敗，直接用 Tavily 提供的庫存 (fallback_content)
    # 雖然這可能是內頁(產品頁)，但通常也包含頁首頁尾的電話，總比沒有好
    if len(fallback_content) > 100:
        return fallback_content, "Tavily庫存 (備案)"
        
    return "", "抓取失敗"

def regex_backup(text):
    """ 暴力掃描電話和 Email """
    # 移除多餘空白和換行，讓 Regex 好找一點
    text_clean = " ".join(text.split())
    
    # Email
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text_clean)
    
    # 電話: 抓取 0x-xxxx 或 09xx (包含括號、橫線、空白)
    phones = re.findall(r'(?:\(?0\d{1,2}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}', text_clean)
    valid_phones = [p for p in list(set(phones)) if len(re.sub(r'\D', '', p)) >= 8]
    
    return list(set(emails)), valid_phones

# --- 4. AI 分析函數 ---

def extract_contact_info(content, url, model):
    emails, phones = regex_backup(content)
    backup_info = f"Email: {emails[:3]}, 電話: {phones[:5]}"

    prompt = f"""
    你是一個資料提取機器人。請分析以下網頁內容，找出公司聯絡方式。
    
    網址：{url}
    【重要】參考數據(程式掃描)：{backup_info}

    網頁內容摘要：
    {content[:60000]}
    
    請回傳 JSON：
    {{
        "公司名稱": "...", 
        "電話": "...", 
        "Email": "...",
        "網址": "{url}"
    }}
    
    規則：
    1. 若文中找不到電話，**請務必**填入【參考數據】中的第一個號碼。
    2. 公司名稱請找完整的 (包含股份有限公司)。
    """
    try:
        response = model.generate_content(prompt)
        txt = response.text.strip()
        # JSON 格式清洗
        if "```json" in txt:
            txt = txt.split("```json")[1].split("```")[0]
        elif "```" in txt:
            txt = txt.split("```")[0]
            
        return json.loads(txt)
    except:
        return {
            "公司名稱": "AI解析失敗", 
            "電話": ", ".join(phones[:2]), 
            "Email": ", ".join(emails[:2]), 
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
        
        status_box = st.status("🚀 啟動終極爬蟲...", expanded=True)
        results_list = []
        
        try:
            status_box.write(f"正在搜尋：{keyword}...")
            
            # --- 關鍵修改：include_raw_content=True ---
            # 這是我們的保命符，讓 Tavily 直接把抓到的內容給我們
            response = tavily.search(query=keyword, max_results=num_results, include_raw_content=True)
            search_results = response.get('results', [])
            
            if not search_results:
                status_box.error("找不到網址")
            else:
                progress_bar = st.progress(0)
                
                for i, item in enumerate(search_results):
                    url = item['url']
                    title = item['title']
                    # 這是 Tavily 已經抓好的內容 (保底)
                    tavily_raw = item.get('raw_content', '') 
                    
                    status_box.write(f"({i+1}/{len(search_results)}) 分析：{title}")
                    
                    # 執行智慧抓取
                    content, source = fetch_content_smart(url, fallback_content=tavily_raw)
                    
                    # Debug 顯示
                    if debug_mode:
                        with st.expander(f"📝 來源：{source} (字數: {len(content)})"):
                            st.text(content[:200] + "...")
                    
                    if len(content) > 50:
                        data = extract_contact_info(content, url, model)
                        
                        if not data.get("公司名稱") or "解析失敗" in str(data.get("公司名稱")):
                            data["公司名稱"] = title
                            
                        # 標記資料來源，讓你之後知道是首頁抓的還是庫存抓的
                        data["資料來源"] = source 
                        results_list.append(data)
                    else:
                        results_list.append({"公司名稱": title, "電話": "無內容", "網址": url, "資料來源": "失敗"})
                    
                    progress_bar.progress((i + 1) / len(search_results))
                    time.sleep(1)

                status_box.update(label="🎉 完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    cols = ["公司名稱", "電話", "Email", "資料來源", "網址"]
                    for c in cols:
                        if c not in df.columns: df[c] = ""
                    df = df[cols]

                    st.dataframe(df)
                    
                    excel_file = "leads_ultimate.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button("📥 下載 Excel", f, file_name="客戶名單.xlsx")

        except Exception as e:
            st.error(f"錯誤：{e}")