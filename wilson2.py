import streamlit as st
import google.generativeai as genai
import pandas as pd
import json
import time
from tavily import TavilyClient

# --- 1. 頁面設定 ---
st.set_page_config(page_title="超級業務開發助手 (Pro Max版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (防封鎖+智慧過濾版)")
st.markdown("""
### 🚀 功能升級說明：
1. **防封鎖機制**：不再自己抓網頁，改用 Tavily 強力抓取，解決「AI 無法讀取」問題。
2. **智慧過濾**：自動剔除「Top 10 懶人包」、「目錄網站」，只留真正的公司官網。
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")
    tavily_api_key = st.text_input("輸入 Tavily API Key", type="password")
    # 建議少量多次，因為開啟 raw_content 比較耗時
    num_results = st.slider("要分析幾個搜尋結果？", 5, 10, 5) 

# --- 3. 核心功能函數 ---

def extract_contact_info(content, url, model):
    prompt = f"""
    你是一個嚴格的資料過濾專家。請分析以下的網頁內容。
    
    目標網址：{url}
    網頁內容摘要：{content[:30000]} 

    請執行以下判斷步驟：
    1. **判斷是否為單一公司官網**：
       - 如果這是「Top 10 推薦」、「懶人包」、「設計師列表(Directory)」、「文章(Blog)」，請直接回傳 null。
       - 如果這是某一家特定公司的首頁或聯絡頁，請繼續提取。

    2. **提取資訊 (若為公司官網)**：
       - 公司名稱 (Company Name)
       - 電話 (Phone)
       - 傳真 (Fax) - 若無留空
       - Email - 若無留空
       - 網址 (URL) - 回傳：{url}
    
    請嚴格回傳 JSON 格式。
    - 如果是公司官網，回傳格式：{{"is_company": true, "data": {{"公司名稱": "...", "電話": "...", "傳真": "...", "Email": "...", "網址": "..."}}}}
    - 如果不是公司官網(是文章或列表)，回傳格式：{{"is_company": false}}
    """
    try:
        response = model.generate_content(prompt)
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_json)
    except Exception as e:
        # 如果解析失敗，我們假設它不是我們要的資料
        return {"is_company": False}

# --- 4. 主程式邏輯 ---
keyword = st.text_input("🔍 請輸入搜尋關鍵字", placeholder="例如：台北 室內設計公司", value="台北 室內設計公司")

if st.button("開始搜尋與分析"):
    if not gemini_api_key or not tavily_api_key:
        st.error("❌ 請在左側輸入 API Key")
    else:
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        tavily = TavilyClient(api_key=tavily_api_key)
        
        status_box = st.status("🚀 啟動 Tavily 強力搜尋引擎...", expanded=True)
        results_list = []
        
        try:
            # 重點修改：include_raw_content=True (讓 Tavily 幫我們抓網頁，不被擋)
            response = tavily.search(query=keyword, max_results=num_results, include_raw_content=True)
            search_results = response.get('results', [])
            
            if not search_results:
                status_box.error("找不到結果。")
            else:
                status_box.write(f"✅ 搜尋完成，開始智慧過濾 {len(search_results)} 筆資料...")
                progress_bar = st.progress(0)
                
                for i, item in enumerate(search_results):
                    title = item['title']
                    url = item['url']
                    # 優先使用 raw_content (完整內容)，沒有的話用 content (摘要)
                    page_content = item.get('raw_content', item.get('content', ''))
                    
                    status_box.write(f"({i+1}/{len(search_results)}) 分析中：{title}")
                    
                    if page_content:
                        ai_result = extract_contact_info(page_content, url, model)
                        
                        # 只有當 AI 說 "is_company": true 時，我們才收錄
                        if ai_result.get("is_company") == True:
                            data = ai_result.get("data")
                            results_list.append(data)
                            status_box.write(f"✨ 成功提取：{data.get('公司名稱')}")
                        else:
                            # 默默跳過非公司網頁
                            pass 
                    
                    progress_bar.progress((i + 1) / len(search_results))
                    time.sleep(1) 

                status_box.update(label="🎉 任務完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    st.subheader(f"📊 成功搜集到 {len(df)} 家公司")
                    st.dataframe(df)
                    
                    excel_file = "leads_data.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button(
                            label="📥 下載過濾後的名單",
                            data=f,
                            file_name=f"{keyword}_精選名單.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                else:
                    st.warning("雖然有搜尋到網頁，但 AI 判斷前幾名都是「懶人包文章」而非「公司官網」。建議：\n1. 增加搜尋數量 (Slider 拉大)\n2. 換個關鍵字，例如「XX公司 官方網站」")

        except Exception as e:
            st.error(f"發生錯誤：{e}")