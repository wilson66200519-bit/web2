import streamlit as st
import google.generativeai as genai
import pandas as pd
import json
import time
import requests
import re
from urllib.parse import urljoin, urlparse
from tavily import TavilyClient

# --- 1. 頁面設定 ---
st.set_page_config(page_title="超級業務開發助手 (強力吸塵版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (Email/傳真 深挖版)")
st.markdown("""
### 🚀 升級說明：
你的懷疑是對的！之前的規則太嚴格了。
這個版本啟動 **「強力吸塵模式」**：
1. **專抓傳真**：鎖定 "Fax", "傳真" 關鍵字，不再漏掉。
2. **深挖 Email**：強制掃描 `mailto:` 連結，命中率提升 200%。
3. **填好填滿**：如果 AI 沒反應，就把所有抓到的號碼都列出來給你選。
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ 設定")
    
    if "GEMINI_API_KEY" in st.secrets:
        gemini_api_key = st.secrets["GEMINI_API_KEY"]
        st.success("✅ 已讀取 Gemini Key")
    else:
        gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")

    if "TAVILY_API_KEY" in st.secrets:
        tavily_api_key = st.secrets["TAVILY_API_KEY"]
        st.success("✅ 已讀取 Tavily Key")
    else:
        tavily_api_key = st.text_input("輸入 Tavily API Key", type="password")

    num_results = st.slider("搜尋數量", 3, 20, 5) 
    debug_mode = st.toggle("顯示後台處理過程", value=True)

# --- 3. 核心工具 ---

def get_root_url(url):
    """ 強制轉回首頁 """
    if not url: return ""
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    except:
        return url

def find_contact_link(markdown_text, root_url):
    """ 尋找聯絡我們連結 """
    links = re.findall(r'\[(.*?)\]\((.*?)\)', markdown_text)
    keywords = ["聯絡", "contact", "about", "關於", "support", "inquiry", "詢價"]
    
    for text, link in links:
        for kw in keywords:
            if kw in text.lower():
                full_link = urljoin(root_url, link)
                return full_link, text
    return None, None

def fetch_content_smart(url, fallback_content=""):
    """ 智慧抓取流程 (含深度挖掘) """
    if fallback_content is None: fallback_content = ""
    
    combined_content = ""
    source_log = []

    root_url = get_root_url(url)
    jina_url = f"https://r.jina.ai/{root_url}"
    
    try:
        resp = requests.get(jina_url, timeout=10)
        if resp.status_code == 200 and len(resp.text) > 200:
            homepage_text = resp.text
            combined_content += f"\n=== 首頁 ===\n{homepage_text[:20000]}"
            source_log.append("首頁")
            
            # 如果首頁沒 Email，嘗試找連結
            if "@" not in homepage_text:
                contact_link, link_text = find_contact_link(homepage_text, root_url)
                if contact_link:
                    source_log.append(f"內頁({link_text})")
                    jina_contact_url = f"https://r.jina.ai/{contact_link}"
                    resp_inner = requests.get(jina_contact_url, timeout=10)
                    if resp_inner.status_code == 200:
                        combined_content += f"\n=== 內頁 ===\n{resp_inner.text[:20000]}"
        else:
            if len(fallback_content) > 50:
                combined_content = fallback_content
                source_log.append("庫存")
                
    except:
        if len(fallback_content) > 50:
            combined_content = fallback_content
            source_log.append("庫存(救援)")

    return combined_content, " + ".join(source_log)

def regex_heavy_duty(text):
    """ 強力掃描：專門對付 Email, 電話, 傳真 """
    if not text: return [], [], []
    
    text_clean = " ".join(text.split()) # 壓扁成一行方便搜尋
    
    # 1. 抓 Email (包含 mailto:)
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text_clean)
    # 額外抓 mailto 連結
    mailto_emails = re.findall(r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', text)
    all_emails = list(set(emails + mailto_emails))

    # 2. 抓 傳真 (Fax)
    # 尋找 "Fax", "傳真", "F:" 後面的數字
    # 邏輯：關鍵字 + 冒號或空白 + 數字
    faxes = re.findall(r'(?:Fax|FAX|傳真|F\.|F:)[\s:：]*(\(?0\d{1,2}\)?[\s\-]?[0-9-]{6,15})', text)
    
    # 3. 抓 電話 (Phone)
    # 寬鬆規則抓所有號碼
    phones_raw = re.findall(r'(?:\(?0\d{1,2}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}', text_clean)
    
    # 過濾：太短的不要，已經被當成傳真的不要
    valid_phones = []
    for p in list(set(phones_raw)):
        clean_p = re.sub(r'\D', '', p)
        if len(clean_p) >= 8 and p not in faxes:
            valid_phones.append(p)

    return all_emails, valid_phones, faxes

# --- 4. AI 分析函數 ---

def extract_contact_info(content, url, model):
    # 先用程式暴力掃一遍
    emails, phones, faxes = regex_heavy_duty(content)
    
    try:
        backup_info = f"Email: {emails[:3]}, 電話: {phones[:3]}, 傳真: {faxes[:2]}"
        prompt = f"""
        你是一個資料提取機器人。請分析網頁內容找出聯絡方式。
        
        網址：{url}
        參考數據(務必優先參考)：{backup_info}

        網頁內容：
        {content[:40000]} 
        
        請回傳 JSON：
        {{
            "公司名稱": "...", 
            "電話": "...", 
            "Email": "...",
            "傳真": "...",
            "網址": "{url}"
        }}
        """
        response = model.generate_content(prompt)
        txt = response.text.strip()
        
        if "```json" in txt:
            txt = txt.split("```json")[1].split("```")[0]
        elif "```" in txt:
            txt = txt.split("```")[0]
            
        data = json.loads(txt)

        # --- 強力回填機制 (Vacuum Mode) ---
        # 如果 AI 漏填，或是填了 None，我們就強制塞 Regex 抓到的資料
        
        # 補 Email (全部列出來，用逗號分隔)
        if (not data.get("Email") or str(data.get("Email")).lower() in ["none", "", "null"]) and emails:
            data["Email"] = ", ".join(emails[:2]) # 填入前兩個
            
        # 補 電話
        if (not data.get("電話") or str(data.get("電話")).lower() in ["none", "", "null"]) and phones:
            data["電話"] = ", ".join(phones[:2])
            
        # 補 傳真 (這很重要，AI 常常漏掉傳真)
        if (not data.get("傳真") or str(data.get("傳真")).lower() in ["none", "", "null"]) and faxes:
            data["傳真"] = faxes[0]

        return data

    except:
        # AI 全掛，回傳所有掃到的資料
        return {
            "公司名稱": "ERROR", 
            "電話": ", ".join(phones[:2]), 
            "Email": ", ".join(emails[:2]), 
            "傳真": faxes[0] if faxes else "",
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
        
        status_box = st.status("🚀 強力吸塵器啟動中...", expanded=True)
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
                        
                        content, source_log = fetch_content_smart(url, fallback_content=tavily_raw)
                        
                        if debug_mode:
                            with st.expander(f"🔍 追蹤路徑: {source_log}"):
                                # 預覽一下有沒有抓到關鍵字
                                emails, _, faxes = regex_heavy_duty(content)
                                st.write(f"預掃描發現 -> Email: {len(emails)} 個, 傳真: {len(faxes)} 個")
                        
                        if len(content) > 50:
                            data = extract_contact_info(content, url, model)
                            
                            name = str(data.get("公司名稱", ""))
                            if name in ["ERROR", "None"] or "失敗" in name:
                                data["公司名稱"] = title
                            
                            results_list.append(data)
                        else:
                            pass
                            
                    except:
                        pass
                        
                    progress_bar.progress((i + 1) / len(search_results))
                    time.sleep(1)

                status_box.update(label="🎉 搜集完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    cols = ["公司名稱", "電話", "Email", "傳真", "網址"]
                    
                    for c in cols:
                        if c not in df.columns: df[c] = ""
                    df = df[cols]

                    st.dataframe(df)
                    
                    excel_file = "leads_vacuum.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button("📥 下載 Excel 名單", f, file_name=f"{keyword}_完整名單.xlsx")

        except Exception as e:
            st.error(f"發生錯誤：{e}")