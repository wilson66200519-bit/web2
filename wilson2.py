import streamlit as st
import google.generativeai as genai
import pandas as pd
import json
import time
import requests
import re
from tavily import TavilyClient

# --- 1. 頁面設定 ---
st.set_page_config(page_title="超級業務開發助手 (Jina版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (Jina 清洗版)")
st.markdown("""
### 🚀 核心升級：
引入 **Jina AI Reader**。它會先把網頁像「剝皮」一樣去掉廣告和雜訊，只把乾淨的文字餵給 AI。
**這能解決 90%「抓不到資料」的問題。**
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")
    tavily_api_key = st.text_input("輸入 Tavily API Key", type="password")
    num_results = st.slider("搜尋數量", 5, 15, 5) 

# --- 3. 核心工具：Jina 清洗與 Regex ---

def get_jina_content(target_url):
    """
    使用 Jina Reader 將網頁轉為乾淨的 Markdown
    """
    jina_url = f"https://r.jina.ai/{target_url}"
    headers = {
        "Authorization": "Bearer ", # 免費版不需要 Key，但留著欄位
        "X-Return-Format": "markdown"
    }
    try:
        response = requests.get(jina_url, headers=headers, timeout=20)
        return response.text
    except:
        return ""

def regex_backup(text):
    """
    暴力掃描電話和 Email
    """
    # 抓 Email
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    # 抓 電話 (寬鬆規則，包含手機與市話)
    phones = re.findall(r'\(?0\d{1,3}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}', text)
    
    return {
        "emails": list(set(emails)),
        "phones": list(set(phones))
    }

# --- 4. AI 分析函數 ---

def extract_contact_info(markdown_text, url, model):
    # 先做物理備份
    backup = regex_backup(markdown_text)
    backup_info = f"備用掃描結果 - Email: {backup['emails'][:3]}, 電話: {backup['phones'][:3]}"

    prompt = f"""
    你是一個資料提取專家。以下是由 Jina Reader 轉換的網頁內容 (Markdown 格式)。
    請從中提取公司聯絡資訊。

    目標網址：{url}
    參考備用數據(Regex掃描)：{backup_info}

    網頁內容：
    {markdown_text[:100000]} 
    
    請回傳 JSON 格式 (若找不到，請參考上面的備用數據填入)：
    {{
        "公司名稱": "...", 
        "電話": "...", 
        "Email": "...", 
        "傳真": "...", 
        "網址": "{url}"
    }}
    """
    try:
        response = model.generate_content(prompt)
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_json)
    except:
        # 萬一 AI 還是失敗，回傳 Regex 的結果
        return {
            "公司名稱": "AI解析失敗", 
            "電話": ", ".join(backup['phones'][:2]), 
            "Email": ", ".join(backup['emails'][:2]), 
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
        
        status_box = st.status("🚀 啟動 Jina 強力清洗模式...", expanded=True)
        results_list = []
        
        try:
            # 1. 用 Tavily 找網址
            status_box.write(f"正在搜尋：{keyword}...")
            # 這裡我們不需要 raw_content 了，只要網址就好
            response = tavily.search(query=keyword, max_results=num_results)
            search_results = response.get('results', [])
            
            if not search_results:
                status_box.error("找不到網址")
            else:
                progress_bar = st.progress(0)
                
                for i, item in enumerate(search_results):
                    url = item['url']
                    title = item['title']
                    
                    status_box.write(f"({i+1}/{len(search_results)}) 正在清洗並分析：{title}")
                    
                    # 2. 用 Jina 抓取乾淨內容
                    clean_content = get_jina_content(url)
                    
                    if len(clean_content) > 100: # 確保有抓到東西
                        # 3. 丟給 AI
                        data = extract_contact_info(clean_content, url, model)
                        
                        # 補名
                        if not data.get("公司名稱") or "解析失敗" in str(data.get("公司名稱")):
                            data["公司名稱"] = title
                            
                        results_list.append(data)
                    else:
                        results_list.append({"公司名稱": title, "網址": url, "備註": "網頁無法讀取"})
                    
                    progress_bar.progress((i + 1) / len(search_results))
                    time.sleep(1) # 禮貌性暫停

                status_box.update(label="🎉 完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    # 欄位整理
                    cols = ["公司名稱", "電話", "Email", "傳真", "網址"]
                    for c in cols:
                        if c not in df.columns: df[c] = ""
                    df = df[cols]

                    st.dataframe(df)
                    
                    excel_file = "leads_jina.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button("📥 下載 Excel", f, file_name="客戶名單_Jina版.xlsx")

        except Exception as e:
            st.error(f"錯誤：{e}")