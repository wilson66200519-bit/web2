import streamlit as st
import google.generativeai as genai
import requests
import pandas as pd
import json
import time
from duckduckgo_search import DDGS  # <--- 這裡換成新的搜尋套件

# --- 1. 頁面設定 ---
st.set_page_config(page_title="超級業務開發助手", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (DDG版)")
st.markdown("輸入關鍵字 (例如：`台北 室內設計公司`)，AI 自動幫你搜集前 10 家公司的聯絡方式。")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    user_api_key = st.text_input("輸入 Gemini API Key", type="password")
    num_results = st.slider("要抓幾家公司？", 5, 20, 10)
    
    st.info("💡 搜尋引擎已切換為 DuckDuckGo，抓取更穩定！")

# --- 3. 核心功能函數 ---

# A. 用 AI 分析網頁內容 (維持不變)
def extract_contact_info(html_text, url, model):
    prompt = f"""
    你是一個資料探勘專家。請從下方的 HTML 原始碼中，提取這家公司的聯絡資訊。
    
    目標網址：{url}
    
    請尋找以下欄位：
    1. 公司名稱 (Company Name) - 若找不到，請從網頁標題推測
    2. 電話 (Phone)
    3. 傳真 (Fax) - 若無則留空
    4. Email - 若無則留空
    5. 網址 (URL) - 回傳：{url}
    
    HTML 內容摘要：{html_text[:50000]} 
    
    請嚴格回傳 JSON 格式，不要有 markdown 標記，格式如下：
    {{
        "公司名稱": "...",
        "網址": "...",
        "電話": "...",
        "傳真": "...",
        "Email": "..."
    }}
    """
    try:
        response = model.generate_content(prompt)
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_json)
    except Exception as e:
        return {"公司名稱": "解析失敗", "網址": url, "錯誤訊息": "AI 無法讀取"}

# B. 爬取單一網頁 (維持不變)
def fetch_page_content(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.encoding = response.apparent_encoding
        return response.text
    except:
        return None

# --- 4. 主程式邏輯 ---
keyword = st.text_input("🔍 請輸入搜尋關鍵字", placeholder="例如：台中 精密機械廠")

if st.button("開始搜尋與分析"):
    if not user_api_key:
        st.error("❌ 請先在左側輸入 Gemini API Key")
    elif not keyword:
        st.warning("⚠️ 請輸入關鍵字")
    else:
        # 設定 AI
        genai.configure(api_key=user_api_key)
        # 使用最新的免費模型
        model = genai.GenerativeModel('gemini-1.5-flash')
        
        status_box = st.status("🚀 任務啟動中...", expanded=True)
        results_list = []
        
        # --- 第一階段：改用 DuckDuckGo 搜尋 ---
        status_box.write(f"正在搜尋：{keyword}...")
        
        search_urls = []
        try:
            with DDGS() as ddgs:
                # region='tw-tzh' 代表搜尋台灣地區
                ddgs_gen = ddgs.text(keyword, max_results=num_results, backend="html")
                for r in ddgs_gen:
                    search_urls.append(r['href'])
            
            if len(search_urls) == 0:
                status_box.error("找不到任何結果，請嘗試其他關鍵字。")
            else:
                status_box.write(f"✅ 找到 {len(search_urls)} 個網址，準備開始逐一分析...")
                
                # 建立進度條
                progress_bar = st.progress(0)
                
                # --- 第二階段：逐一爬取 ---
                for i, url in enumerate(search_urls):
                    status_box.write(f"({i+1}/{len(search_urls)}) 正在分析：{url}")
                    
                    # 1. 抓網頁
                    html_content = fetch_page_content(url)
                    
                    if html_content:
                        # 2. AI 提取
                        data = extract_contact_info(html_content, url, model)
                        results_list.append(data)
                    else:
                        results_list.append({
                            "公司名稱": "網頁無法開啟",
                            "網址": url,
                            "電話": "", "傳真": "", "Email": ""
                        })
                    
                    # 更新進度條
                    progress_bar.progress((i + 1) / len(search_urls))
                    time.sleep(1) # 休息一下

                status_box.update(label="🎉 分析完成！", state="complete", expanded=False)
                
                # --- 5. 顯示結果與匯出 ---
                if results_list:
                    df = pd.DataFrame(results_list)
                    
                    st.subheader("📊 搜尋結果")
                    st.dataframe(df)
                    
                    # Excel 下載
                    excel_file = "leads_data.xlsx"
                    df.to_excel(excel_file, index=False)
                    
                    with open(excel_file, "rb") as f:
                        st.download_button(
                            label="📥 下載 Excel 名單",
                            data=f,
                            file_name=f"{keyword}_客戶名單.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )

        except Exception as e:
            st.error(f"搜尋發生錯誤：{e}")