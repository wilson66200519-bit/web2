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
st.set_page_config(page_title="超級業務開發助手 (不倒翁版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (永不當機版)")
st.markdown("""
### 🛡️ 穩定性承諾：
這版本加入了 **「迴圈獨立保護」** 機制。
即使某個網站導致錯誤，程式會自動記錄並**跳過該筆**，繼續執行下一筆。
**保證任務一定會執行到最後！**
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")
    tavily_api_key = st.text_input("輸入 Tavily API Key", type="password")
    num_results = st.slider("搜尋數量", 3, 20, 5) 
    debug_mode = st.toggle("顯示除錯訊息 (Debug)", value=True)

# --- 3. 核心工具 ---

def get_root_url(url):
    """ 強制轉回首頁 (含防呆) """
    if not url: return ""
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    except:
        return url

def fetch_content_smart(url, fallback_content=""):
    """
    智慧抓取流程：Jina -> Tavily庫存
    """
    # [防呆] 確保 fallback 不是 None
    if fallback_content is None:
        fallback_content = ""

    # 嘗試 1: 用 Jina 抓首頁
    try:
        target_url = get_root_url(url)
        jina_url = f"https://r.jina.ai/{target_url}"
        resp = requests.get(jina_url, timeout=10)
        if resp.status_code == 200 and len(resp.text) > 200:
            return resp.text, "Jina (首頁)"
    except:
        pass # 失敗就默默略過
    
    # 嘗試 2: 用 Tavily 庫存
    if len(fallback_content) > 50:
        return fallback_content, "Tavily庫存"
        
    return "", "抓取失敗"

def regex_backup(text):
    """ 暴力掃描電話和 Email """
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
    try:
        emails, phones = regex_backup(content)
        backup_info = f"Email: {emails[:3]}, 電話: {phones[:5]}"

        prompt = f"""
        你是一個資料提取機器人。請分析以下網頁內容，找出公司聯絡方式。
        
        網址：{url}
        參考數據：{backup_info}

        網頁內容摘要：
        {content[:60000]}
        
        請回傳 JSON：
        {{
            "公司名稱": "...", 
            "電話": "...", 
            "Email": "...",
            "網址": "{url}"
        }}
        若找不到，請填入參考數據。
        """
        response = model.generate_content(prompt)
        txt = response.text.strip()
        
        # JSON 清洗
        if "```json" in txt:
            txt = txt.split("```json")[1].split("```")[0]
        elif "```" in txt:
            txt = txt.split("```")[0]
            
        return json.loads(txt)
    except Exception as e:
        # 這裡也加了防護，AI 失敗就回傳基本資料
        return {
            "公司名稱": "AI解析失敗", 
            "電話": "", 
            "Email": "", 
            "網址": url,
            "備註": str(e)
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
        
        status_box = st.status("🚀 任務啟動...", expanded=True)
        results_list = []
        
        # [最外層保護] 搜尋階段
        try:
            status_box.write(f"正在搜尋：{keyword}...")
            response = tavily.search(query=keyword, max_results=num_results, include_raw_content=True)
            search_results = response.get('results', [])
            
            if not search_results:
                status_box.error("找不到網址")
            else:
                progress_bar = st.progress(0)
                
                # --- [關鍵改進] 迴圈內部保護 ---
                for i, item in enumerate(search_results):
                    try: 
                        # 每一筆資料都獨立處理，一筆失敗不會影響下一筆
                        url = item.get('url', '無網址')
                        title = item.get('title', '無標題')
                        tavily_raw = item.get('raw_content') or "" # 再次確保不是 None
                        
                        status_box.write(f"({i+1}/{len(search_results)}) 分析：{title}")
                        
                        # 執行抓取
                        content, source = fetch_content_smart(url, fallback_content=tavily_raw)
                        
                        if debug_mode:
                            with st.expander(f"📝 {title} 來源: {source}"):
                                st.text(content[:100] + "...")
                        
                        if len(content) > 50:
                            data = extract_contact_info(content, url, model)
                            # 補標題
                            if not data.get("公司名稱") or "解析失敗" in str(data.get("公司名稱")):
                                data["公司名稱"] = title
                            
                            data["資料來源"] = source
                            results_list.append(data)
                        else:
                            results_list.append({"公司名稱": title, "電話": "無內容", "網址": url, "資料來源": "失敗"})
                            
                    except Exception as inner_e:
                        # 萬一這一筆真的爆炸了，印出錯誤，但繼續下一筆！
                        st.warning(f"⚠️ 第 {i+1} 筆資料發生未知錯誤，已跳過：{inner_e}")
                        results_list.append({"公司名稱": title, "備註": "系統跳過", "網址": url})
                        
                    # 更新進度條
                    progress_bar.progress((i + 1) / len(search_results))
                    time.sleep(0.5)

                status_box.update(label="🎉 任務全部完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    # 確保欄位存在
                    cols = ["公司名稱", "電話", "Email", "資料來源", "網址", "備註"]
                    for c in cols:
                        if c not in df.columns: df[c] = ""
                    df = df[cols]

                    st.dataframe(df)
                    
                    excel_file = "leads_stable.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button("📥 下載 Excel", f, file_name="客戶名單_穩定版.xlsx")

        except Exception as e:
            st.error(f"搜尋階段發生嚴重錯誤：{e}")