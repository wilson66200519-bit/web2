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
st.set_page_config(page_title="超級業務開發助手 (Email加強版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (Email/傳真 強制回填版)")
st.markdown("""
### 🛡️ 版本更新：
1. **Email 強制回填**：如果 AI 漏抓 Email，程式會自動把掃描到的 Email 補上去。
2. **修復 404 錯誤**：使用穩定的 `gemini-pro` 模型。
3. **欄位齊全**：電話、Email、傳真 全部都會顯示。
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    gemini_api_key = st.text_input("輸入 Gemini API Key", type="password", value="AIzaSyBdCdsWy8slsyC2XKzkfUWANh94Y96y5XU")
    tavily_api_key = st.text_input("輸入 Tavily API Key", type="password", value="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")
    num_results = st.slider("搜尋數量", 3, 20, 5) 
    debug_mode = st.toggle("顯示除錯訊息 (Debug)", value=True)

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
            return resp.text, "Jina (首頁)"
    except:
        pass 
    
    # 嘗試 2: Tavily 庫存
    if len(fallback_content) > 50:
        return fallback_content, "Tavily庫存"
        
    return "", "抓取失敗"

def regex_backup(text):
    """ 暴力掃描 Email 和 電話 """
    if not text: return [], []
    try:
        text_clean = " ".join(text.split())
        # 嚴格一點的 Email 規則
        emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text_clean)
        phones = re.findall(r'(?:\(?0\d{1,2}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}', text_clean)
        valid_phones = [p for p in list(set(phones)) if len(re.sub(r'\D', '', p)) >= 8]
        return list(set(emails)), valid_phones
    except:
        return [], []

# --- 4. AI 分析函數 ---

def extract_contact_info(content, url, model):
    try:
        # 先用程式掃描一次
        emails, phones = regex_backup(content)
        backup_info = f"Email: {emails[:3]}, 電話: {phones[:5]}"

        prompt = f"""
        你是一個資料提取機器人。請分析以下網頁內容，找出公司聯絡方式。
        
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
        若找不到，請填入參考數據。公司名稱請找最像官方名稱的。
        """
        response = model.generate_content(prompt)
        txt = response.text.strip()
        
        if "```json" in txt:
            txt = txt.split("```json")[1].split("```")[0]
        elif "```" in txt:
            txt = txt.split("```")[0]
            
        data = json.loads(txt)

        # --- [關鍵新增] Python 強制回填機制 ---
        # 如果 AI 還是回傳空值，或是回傳 None，我們手動把掃到的 Email 塞進去
        if (not data.get("Email") or data.get("Email") == "None") and emails:
            data["Email"] = emails[0]  # 取第一個掃到的 Email
            
        if (not data.get("電話") or data.get("電話") == "None") and phones:
            data["電話"] = phones[0]   # 取第一個掃到的電話

        return data

    except Exception as e:
        # 萬一 AI 壞掉，至少回傳 regex 掃到的東西
        emails, phones = regex_backup(content)
        return {
            "公司名稱": "AI解析失敗", 
            "電話": phones[0] if phones else "", 
            "Email": emails[0] if emails else "", 
            "傳真": "",
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
        
        # 使用相容性最好的模型
        model = genai.GenerativeModel('gemini-pro')
        
        tavily = TavilyClient(api_key=tavily_api_key)
        
        status_box = st.status("🚀 任務啟動...", expanded=True)
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
                        
                        content, source = fetch_content_smart(url, fallback_content=tavily_raw)
                        
                        if debug_mode:
                            with st.expander(f"📝 {title} 來源: {source}"):
                                st.text(content[:100] + "...")
                        
                        if len(content) > 50:
                            data = extract_contact_info(content, url, model)
                            if not data.get("公司名稱") or "解析失敗" in str(data.get("公司名稱")):
                                data["公司名稱"] = title
                            
                            data["資料來源"] = source
                            results_list.append(data)
                        else:
                            results_list.append({"公司名稱": title, "電話": "無內容", "資料來源": "失敗"})
                            
                    except Exception as inner_e:
                        results_list.append({"公司名稱": title, "備註": f"跳過: {inner_e}"})
                        
                    progress_bar.progress((i + 1) / len(search_results))
                    time.sleep(0.5)

                status_box.update(label="🎉 任務全部完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    
                    # --- [這裡確保 Email 和 傳真 一定會顯示] ---
                    cols = ["公司名稱", "電話", "Email", "傳真", "資料來源", "網址", "備註"]
                    for c in cols:
                        if c not in df.columns: df[c] = ""
                    df = df[cols]

                    st.dataframe(df)
                    
                    excel_file = "leads_email_fixed.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button("📥 下載 Excel", f, file_name="客戶名單_完整版.xlsx")

        except Exception as e:
            st.error(f"錯誤：{e}")