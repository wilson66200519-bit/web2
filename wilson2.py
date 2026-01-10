import streamlit as st
import google.generativeai as genai
import pandas as pd
import json
import time
from tavily import TavilyClient

# --- 1. 頁面設定 ---
st.set_page_config(page_title="超級業務開發助手 (貪婪版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (無差別抓取版)")
st.markdown("""
### 🚀 策略調整：
**不再過濾！** 只要網頁上有聯絡方式，全部抓下來。即使是「黃頁」或「介紹文章」，只要有電話/Email 就不放過。
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")
    tavily_api_key = st.text_input("輸入 Tavily API Key", type="password")
    num_results = st.slider("要分析幾個搜尋結果？", 5, 20, 10) 

# --- 3. 核心功能函數 ---

def extract_contact_info(content, url, model):
    # 修改後的 Prompt：不再要求判斷是不是官網，而是全力挖掘資料
    prompt = f"""
    你是一個資料提取機器人。請分析這段網頁內容，盡可能提取出「主要的公司/機構聯絡資訊」。

    目標網址：{url}
    網頁內容摘要：{content[:30000]} 

    請遵循以下原則：
    1. 如果網頁是「單一公司官網」，抓取該公司的資料。
    2. 如果網頁是「文章」或「名錄」，請抓取**文章中提到的第一家**或**最明顯**的公司資料。
    3. 如果真的完全找不到電話或 Email，才回傳空值。

    請回傳 JSON 格式：
    {{
        "公司名稱": "...", 
        "電話": "...", 
        "傳真": "...", 
        "Email": "...", 
        "網頁類型": "官網" 或 "文章/名錄" (請依你的判斷填寫),
        "網址": "{url}"
    }}
    """
    try:
        response = model.generate_content(prompt)
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_json)
    except Exception as e:
        # 萬一 AI 壞掉，至少回傳網址，不要報錯
        return {
            "公司名稱": "AI 解析失敗", 
            "電話": "", 
            "Email": "", 
            "網頁類型": "未知", 
            "網址": url
        }

# --- 4. 主程式邏輯 ---
keyword = st.text_input("🔍 請輸入搜尋關鍵字", placeholder="例如：台北 室內設計公司", value="台北 室內設計公司")

if st.button("開始搜尋與分析"):
    if not gemini_api_key or not tavily_api_key:
        st.error("❌ 請在左側輸入 API Key")
    else:
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        tavily = TavilyClient(api_key=tavily_api_key)
        
        status_box = st.status("🚀 啟動 Tavily 強力搜尋...", expanded=True)
        results_list = []
        
        try:
            # include_raw_content=True 依然是關鍵
            response = tavily.search(query=keyword, max_results=num_results, include_raw_content=True)
            search_results = response.get('results', [])
            
            if not search_results:
                status_box.error("找不到任何網頁結果。")
            else:
                status_box.write(f"✅ 搜尋到 {len(search_results)} 個網頁，開始全面開採...")
                progress_bar = st.progress(0)
                
                for i, item in enumerate(search_results):
                    title = item['title']
                    url = item['url']
                    # 優先用完整內容，沒有就用摘要
                    page_content = item.get('raw_content', item.get('content', ''))
                    
                    status_box.write(f"({i+1}/{len(search_results)}) 分析中：{title}")
                    
                    if page_content:
                        # 呼叫 AI (這次不管是不是官網，通通收！)
                        data = extract_contact_info(page_content, url, model)
                        
                        # 如果 AI 沒抓到名字，用標題補
                        if not data.get("公司名稱") or data.get("公司名稱") == "AI 解析失敗":
                             data["公司名稱"] = title

                        results_list.append(data)
                    else:
                        results_list.append({"公司名稱": title, "網址": url, "備註": "無法讀取內容"})
                    
                    progress_bar.progress((i + 1) / len(search_results))
                    time.sleep(1) 

                status_box.update(label="🎉 任務完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    
                    # 調整欄位順序讓你看得比較順眼
                    cols = ["公司名稱", "電話", "Email", "網頁類型", "網址", "傳真"]
                    # 確保欄位存在，避免報錯
                    for c in cols:
                        if c not in df.columns:
                            df[c] = ""
                    df = df[cols]

                    st.subheader(f"📊 搜集成果 ({len(df)} 筆)")
                    st.dataframe(df)
                    
                    excel_file = "leads_data_greedy.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button(
                            label="📥 下載 Excel 名單",
                            data=f,
                            file_name=f"{keyword}_無差別名單.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
        except Exception as e:
            st.error(f"發生錯誤：{e}")