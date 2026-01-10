import streamlit as st
import google.generativeai as genai
import pandas as pd
import json
import time
import re  # 新增：正規表達式套件 (用來暴力抓電話跟Email)
from tavily import TavilyClient

# --- 1. 頁面設定 ---
st.set_page_config(page_title="超級業務開發助手 (終極版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (AI + Regex 雙引擎版)")
st.markdown("""
### 🚀 為什麼這個版本最強？
1. **加大視野**：將讀取範圍從 3 萬字擴大到 15 萬字，確保讀得到頁尾 (Footer)。
2. **雙重保險**：如果 AI 漏看，程式會自動用「規則」暴力掃描電話與 Email。
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")
    tavily_api_key = st.text_input("輸入 Tavily API Key", type="password")
    num_results = st.slider("搜尋數量", 5, 20, 10) 
    debug_mode = st.checkbox("顯示除錯資訊 (Debug)", value=False)

# --- 3. 輔助功能：暴力抓取 (Regex) ---
def regex_backup(text):
    # 抓 Email 的規則
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    
    # 抓 台灣電話 的規則 (手機或市話)
    phones = re.findall(r'(0\d{1,2}-?\d{6,8}|09\d{2}-?\d{3}-?\d{3})', text)
    
    return {
        "emails": list(set(emails)), # 去除重複
        "phones": list(set(phones))
    }

# --- 4. 核心功能函數 ---
def extract_contact_info(content, url, model):
    # 1. 先用 Regex 暴力掃描一次作為備用
    backup_data = regex_backup(content)
    backup_email = ", ".join(backup_data["emails"][:3]) # 只取前3個
    backup_phone = ", ".join(backup_data["phones"][:3])

    # 2. 再請 AI 嘗試理解並提取
    prompt = f"""
    你是一個資料提取專家。請分析網頁內容，提取公司聯絡資訊。
    
    目標網址：{url}
    HTML 內容摘要：{content[:150000]}  <-- 我們加大了這裡的額度
    
    請尋找：
    1. 公司名稱 (若找不到，請用網頁標題或內文最顯著的名稱)
    2. 電話
    3. Email
    4. 傳真
    
    注意：
    - 如果你沒找到電話，但我用程式掃描發現了這些號碼：[{backup_phone}]，請幫我判斷哪個最像公司電話並填入。
    - 如果你沒找到 Email，但我掃描到了：[{backup_email}]，請填入。
    
    請回傳 JSON：
    {{
        "公司名稱": "...", 
        "電話": "...", 
        "Email": "...", 
        "傳真": "...", 
        "網址": "{url}",
        "類型": "官網/文章/未知"
    }}
    """
    try:
        response = model.generate_content(prompt)
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        data = json.loads(clean_json)
        
        # --- 雙重確認機制 ---
        # 如果 AI 還是回傳空值，我們強制把 Regex 抓到的塞進去
        if not data.get("Email") and backup_data["emails"]:
            data["Email"] = backup_data["emails"][0]
        if not data.get("電話") and backup_data["phones"]:
            data["電話"] = backup_data["phones"][0]
            
        return data
        
    except Exception as e:
        # 如果 AI 當機，至少回傳 Regex 抓到的東西
        return {
            "公司名稱": "AI解析失敗(僅顯示抓取數據)", 
            "電話": backup_phone, 
            "Email": backup_email, 
            "傳真": "",
            "網址": url,
            "類型": "備用數據"
        }

# --- 5. 主程式邏輯 ---
keyword = st.text_input("🔍 請輸入搜尋關鍵字", value="廢水回收系統 公司")

if st.button("開始搜尋與分析"):
    if not gemini_api_key or not tavily_api_key:
        st.error("❌ 請在左側輸入 API Key")
    else:
        genai.configure(api_key=gemini_api_key)
        # 使用 Flash 模型，Token 額度大，適合讀長文
        model = genai.GenerativeModel('gemini-1.5-flash')
        tavily = TavilyClient(api_key=tavily_api_key)
        
        status_box = st.status("🚀 啟動雙引擎搜尋...", expanded=True)
        results_list = []
        
        try:
            # 這次我們不只抓 raw_content，也讓 Tavily 幫我們做一點預處理
            response = tavily.search(query=keyword, max_results=num_results, include_raw_content=True)
            search_results = response.get('results', [])
            
            if not search_results:
                status_box.error("找不到任何網頁結果。")
            else:
                status_box.write(f"✅ 搜尋到 {len(search_results)} 個網頁，開始深入挖掘...")
                progress_bar = st.progress(0)
                
                for i, item in enumerate(search_results):
                    title = item['title']
                    url = item['url']
                    # 優先取用 raw_content (HTML)，沒有的話用 content (文字)
                    # 這次我們讀取範圍加大，避免 footer 被切掉
                    page_content = item.get('raw_content', "")
                    if not page_content:
                        page_content = item.get('content', "")

                    status_box.write(f"({i+1}/{len(search_results)}) 分析中：{title}")
                    
                    if page_content:
                        data = extract_contact_info(page_content, url, model)
                        # 補強公司名稱
                        if not data.get("公司名稱") or "解析失敗" in str(data.get("公司名稱")):
                             data["公司名稱"] = title
                        
                        results_list.append(data)
                        
                        if debug_mode:
                            with st.expander(f"除錯：{title}"):
                                st.text(f"AI 回傳結果: {data}")
                    else:
                        results_list.append({"公司名稱": title, "網址": url, "備註": "無法讀取內容"})
                    
                    progress_bar.progress((i + 1) / len(search_results))
                    # Tavily 比較耐操，可以設短一點，加快速度
                    time.sleep(0.5) 

                status_box.update(label="🎉 任務完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    
                    # 整理欄位
                    cols = ["公司名稱", "電話", "Email", "傳真", "網址", "類型"]
                    for c in cols:
                        if c not in df.columns: df[c] = ""
                    df = df[cols]

                    st.subheader(f"📊 搜集成果 ({len(df)} 筆)")
                    st.dataframe(df)
                    
                    excel_file = "leads_final.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button("📥 下載 Excel", f, file_name="客戶名單.xlsx")

        except Exception as e:
            st.error(f"發生錯誤：{e}")