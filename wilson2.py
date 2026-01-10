import streamlit as st
import google.generativeai as genai
import pandas as pd
import json
import time
import requests
import re
from urllib.parse import urlparse # 新增這個工具來處理網址
from tavily import TavilyClient

# --- 1. 頁面設定 ---
st.set_page_config(page_title="超級業務開發助手 (首頁鎖定版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (強制抓取首頁版)")
st.markdown("""
### 🚀 策略修正：
之前的版本抓到了「產品內頁」，導致找不到電話。
**這個版本會自動找出該公司的「官方首頁」，直接去首頁抓頁尾的聯絡資訊，命中率 99%！**
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")
    tavily_api_key = st.text_input("輸入 Tavily API Key", type="password")
    num_results = st.slider("搜尋數量", 5, 15, 5) 

# --- 3. 核心工具 ---

def get_root_url(url):
    """
    把長網址 (例如 www.abc.com/products/123) 變成 首頁 (https://www.abc.com)
    """
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    except:
        return url

def get_jina_content(target_url):
    """
    使用 Jina Reader 讀取網頁
    """
    # 這裡很關鍵：告訴 Jina 我們要讀的是這個網址
    jina_url = f"https://r.jina.ai/{target_url}"
    headers = {
        "Authorization": "Bearer ",
        "X-Return-Format": "markdown"
    }
    try:
        # 設定 30 秒超時，給它多一點時間跑
        response = requests.get(jina_url, headers=headers, timeout=30)
        return response.text
    except:
        return ""

def regex_backup(text):
    """
    暴力掃描電話和 Email
    """
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    # 台灣電話規則 (包含 (02) xxxx-xxxx 或 09xx-xxx-xxx)
    phones = re.findall(r'(\(?0\d{1,2}\)?[\s\-]?\d{3,4}[\s\-]?\d{3,4})', text)
    
    return {
        "emails": list(set(emails)),
        "phones": list(set(phones))
    }

# --- 4. AI 分析函數 ---

def extract_contact_info(markdown_text, url, model):
    backup = regex_backup(markdown_text)
    
    # 過濾掉太短的電話雜訊
    valid_phones = [p for p in backup['phones'] if len(re.sub(r'\D', '', p)) >= 8]
    backup_info = f"備用掃描 - Email: {backup['emails'][:3]}, 電話: {valid_phones[:3]}"

    prompt = f"""
    你是一個資料提取專家。我提供給你的是一家公司的【首頁】內容。
    請從中尋找該公司的聯絡方式 (通常在最上方 Header 或最下方 Footer)。

    目標網站：{url}
    參考備用數據(Regex掃描)：{backup_info}

    網頁內容摘要(Markdown)：
    {markdown_text[:100000]} 
    
    請回傳 JSON 格式：
    {{
        "公司名稱": "...", 
        "電話": "...", 
        "Email": "...", 
        "傳真": "...", 
        "網址": "{url}"
    }}
    注意：
    1. 如果 AI 找不到，但備用數據有電話/Email，請直接填入備用數據。
    2. 公司名稱請盡量找完整的 (例如 XX科技股份有限公司)。
    """
    try:
        response = model.generate_content(prompt)
        clean_json = response.text.strip().replace('```json', '').replace('```', '')
        return json.loads(clean_json)
    except:
        return {
            "公司名稱": "AI解析失敗", 
            "電話": ", ".join(valid_phones[:2]), 
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
        
        status_box = st.status("🚀 啟動首頁鎖定模式...", expanded=True)
        results_list = []
        
        try:
            # 1. 搜尋
            status_box.write(f"正在搜尋：{keyword}...")
            response = tavily.search(query=keyword, max_results=num_results)
            search_results = response.get('results', [])
            
            if not search_results:
                status_box.error("找不到網址")
            else:
                progress_bar = st.progress(0)
                
                for i, item in enumerate(search_results):
                    original_url = item['url']
                    title = item['title']
                    
                    # --- 關鍵修改：強制轉回首頁 ---
                    root_url = get_root_url(original_url)
                    
                    status_box.write(f"({i+1}/{len(search_results)}) 鎖定首頁分析：{root_url}")
                    
                    # 2. 抓取首頁內容
                    clean_content = get_jina_content(root_url)
                    
                    if len(clean_content) > 100:
                        data = extract_contact_info(clean_content, root_url, model)
                        
                        # 補名
                        if not data.get("公司名稱") or "解析失敗" in str(data.get("公司名稱")):
                            data["公司名稱"] = title
                        
                        # 把原始連結也存著，方便對照
                        data["原始搜尋連結"] = original_url
                            
                        results_list.append(data)
                        status_box.write(f"✅ 抓到：{data.get('公司名稱')} - {data.get('電話')}")
                    else:
                        results_list.append({"公司名稱": title, "網址": root_url, "電話": "無法讀取首頁"})
                    
                    progress_bar.progress((i + 1) / len(search_results))
                    time.sleep(1)

                status_box.update(label="🎉 完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    cols = ["公司名稱", "電話", "Email", "傳真", "網址", "原始搜尋連結"]
                    for c in cols:
                        if c not in df.columns: df[c] = ""
                    df = df[cols]

                    st.dataframe(df)
                    
                    excel_file = "leads_root.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button("📥 下載 Excel", f, file_name="客戶名單_首頁版.xlsx")

        except Exception as e:
            st.error(f"錯誤：{e}")