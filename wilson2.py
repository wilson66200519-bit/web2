import streamlit as st
import google.generativeai as genai
import requests
import pandas as pd
import json
import time
from tavily import TavilyClient # 改用這個最強搜尋神器

# --- 1. 頁面設定 ---
st.set_page_config(page_title="超級業務開發助手 (Pro版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (API版)")
st.markdown("使用 Tavily 搜尋引擎，專為 AI 設計，**不再被封鎖**！")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")
    tavily_api_key = st.text_input("輸入 Tavily API Key", type="password", help="去 tavily.com 免費申請")
    num_results = st.slider("要抓幾家公司？", 5, 10, 5) # Tavily 免費版建議少量多次

# --- 3. 核心功能函數 ---

def extract_contact_info(html_text, url, model):
    prompt = f"""
    你是一個資料探勘專家。請從下方的 HTML 原始碼中，提取這家公司的聯絡資訊。
    
    目標網址：{url}
    
    請尋找以下欄位：
    1. 公司名稱 (Company Name)
    2. 電話 (Phone)
    3. 傳真 (Fax) - 若無則留空
    4. Email - 若無則留空
    5. 網址 (URL) - 回傳：{url}
    
    HTML 內容摘要：{html_text[:40000]} 
    
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
keyword = st.text_input("🔍 請輸入搜尋關鍵字", placeholder="例如：台北 室內設計公司")

if st.button("開始搜尋與分析"):
    if not gemini_api_key or not tavily_api_key:
        st.error("❌ 請在左側輸入 Gemini 和 Tavily 的 API Key")
    elif not keyword:
        st.warning("⚠️ 請輸入關鍵字")
    else:
        # 設定 Clients
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        tavily = TavilyClient(api_key=tavily_api_key)
        
        status_box = st.status("🚀 任務啟動中...", expanded=True)
        results_list = []
        
        # --- 第一階段：Tavily 搜尋 ---
        status_box.write(f"正在透過 Tavily 搜尋：{keyword}...")
        
        try:
            # Tavily 的搜尋非常精準，而且支援中文
            response = tavily.search(query=keyword, max_results=num_results)
            search_results = response.get('results', [])
            
            if not search_results:
                status_box.error("找不到結果，請換個關鍵字試試。")
            else:
                status_box.write(f"✅ 成功找到 {len(search_results)} 筆資料！開始爬取詳情...")
                progress_bar = st.progress(0)
                
                # --- 第二階段：逐一爬取 ---
                for i, item in enumerate(search_results):
                    url = item['url']
                    title = item['title']
                    status_box.write(f"({i+1}/{len(search_results)}) 分析中：{title}")
                    
                    # 1. 抓網頁
                    html_content = fetch_page_content(url)
                    
                    if html_content:
                        # 2. AI 提取
                        data = extract_contact_info(html_content, url, model)
                        # 如果 AI 沒抓到名字，用搜尋結果的標題補上去
                        if data.get("公司名稱") in [None, "", "解析失敗"]:
                            data["公司名稱"] = title
                        results_list.append(data)
                    else:
                        results_list.append({
                            "公司名稱": title,
                            "網址": url,
                            "電話": "無法連線", "傳真": "", "Email": ""
                        })
                    
                    progress_bar.progress((i + 1) / len(search_results))
                    time.sleep(1) 

                status_box.update(label="🎉 分析完成！", state="complete", expanded=False)
                
                # --- 5. 顯示結果與匯出 ---
                if results_list:
                    df = pd.DataFrame(results_list)
                    st.subheader("📊 搜尋結果")
                    st.dataframe(df)
                    
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
            st.error(f"發生錯誤：{e}")