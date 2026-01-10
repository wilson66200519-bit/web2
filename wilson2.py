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
st.set_page_config(page_title="超級業務開發助手 (全面追殺版)", layout="wide")
st.title("🕵️‍♂️ 全自動客戶名單搜集器 (全欄位補完版)")
st.markdown("""
### 🚀 既然要補，就全部補齊：
這個版本升級了「二次追殺」機制：
只要 **電話、Email、傳真** 其中任何一個有缺，程式就會發動搜尋。
一次把所有聯絡資訊都挖出來填滿！
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
    """ 尋找內頁連結 """
    links = re.findall(r'\[(.*?)\]\((.*?)\)', markdown_text)
    keywords = [
        "聯絡", "contact", "about", "關於", "support", "inquiry", "詢價", 
        "服務", "service", "map", "location", "據點", "營業", "profile", "簡介"
    ]
    for text, link in links:
        for kw in keywords:
            if kw in text.lower():
                full_link = urljoin(root_url, link)
                return full_link, text
    return None, None

def fetch_content_smart(url, fallback_content=""):
    """ 智慧抓取流程 """
    if fallback_content is None: fallback_content = ""
    combined_content = ""
    source_log = []
    root_url = get_root_url(url)
    jina_url = f"https://r.jina.ai/{root_url}"
    
    try:
        resp = requests.get(jina_url, timeout=10)
        if resp.status_code == 200 and len(resp.text) > 200:
            homepage_text = resp.text
            combined_content += f"\n=== 首頁內容 ===\n{homepage_text[:20000]}"
            source_log.append("首頁")
            if "@" not in homepage_text:
                contact_link, link_text = find_contact_link(homepage_text, root_url)
                if contact_link:
                    source_log.append(f"內頁({link_text})")
                    jina_contact_url = f"https://r.jina.ai/{contact_link}"
                    resp_inner = requests.get(jina_contact_url, timeout=10)
                    if resp_inner.status_code == 200:
                        combined_content += f"\n=== {link_text} ===\n{resp_inner.text[:20000]}"
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
    """ 強力掃描 + 智慧分類 """
    if not text: return [], [], [], []
    text_clean = " ".join(text.split())
    
    # Email
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text_clean)
    mailto_emails = re.findall(r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', text)
    all_emails = list(set(emails + mailto_emails))

    # Fax
    fax_patterns = [
        r'(?:Fax|FAX|傳真|Facsimile|F\.|F:)[\s:：\.]*(\(?0\d{1,2}\)?[\s\-]?[0-9-]{6,15})',
        r'(?:Tel\/Fax|TEL\/FAX)[\s:：\.]*(\(?0\d{1,2}\)?[\s\-]?[0-9-]{6,15})'
    ]
    faxes = []
    for pattern in fax_patterns:
        found = re.findall(pattern, text)
        faxes.extend(found)
    faxes = list(set(faxes))
    
    # Phone / Tax ID
    raw_numbers = re.findall(r'(?:\(?0\d{1,2}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}', text_clean)
    phones = []
    tax_ids = []
    for num in list(set(raw_numbers)):
        clean_num = re.sub(r'\D', '', num)
        is_fax = False
        for f in faxes:
            if clean_num in re.sub(r'\D', '', f):
                is_fax = True; break
        if is_fax: continue

        if len(clean_num) >= 8:
            if clean_num.startswith('0'): phones.append(num)
            elif len(clean_num) == 8: tax_ids.append(clean_num)
            else: phones.append(num)

    return all_emails, phones, faxes, tax_ids

# --- 4. 補刀搜尋功能 (Hunter Mode - 全面版) ---
def hunter_search(company_name, tavily_client):
    """ 
    當缺資料時，專門針對該公司進行全方位搜尋 
    搜尋關鍵字包含：電話、傳真、Email、聯絡方式
    """
    if not company_name or company_name == "Unknown": return ""
    
    # 擴充搜尋關鍵字
    query = f"{company_name} 電話 傳真 email 聯絡方式 contact"
    try:
        resp = tavily_client.search(query=query, max_results=3)
        snippets = ""
        for res in resp.get('results', []):
            snippets += res.get('content', '') + "\n"
        return snippets
    except:
        return ""

# --- 5. AI 分析函數 ---

def extract_contact_info(content, url, model, snippet_content="", company_name_hint=""):
    full_scan_text = content + "\n=== 搜尋摘要 ===\n" + snippet_content
    emails, phones, faxes, tax_ids = regex_heavy_duty(full_scan_text)
    
    try:
        backup_info = f"Email: {emails[:3]}, 電話: {phones[:3]}, 傳真: {faxes[:2]}"
        prompt = f"""
        你是一個資料提取機器人。請找出聯絡方式。
        目標公司：{company_name_hint}
        
        網址：{url}
        參考數據：{backup_info}
        
        網頁內容與摘要：
        {content[:30000]} 
        {snippet_content}
        
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
        if "```json" in txt: txt = txt.split("```json")[1].split("```")[0]
        elif "```" in txt: txt = txt.split("```")[0]
        data = json.loads(txt)

        # 強力回填 (第一階段)
        if (not data.get("Email") or str(data.get("Email")).lower() in ["none", "", "null"]) and emails:
            data["Email"] = ", ".join(emails[:2])
        if (not data.get("電話") or str(data.get("電話")).lower() in ["none", "", "null"]) and phones:
            data["電話"] = ", ".join(phones[:2])
        if (not data.get("傳真") or str(data.get("傳真")).lower() in ["none", "", "null"]) and faxes:
            data["傳真"] = faxes[0]
        if tax_ids:
            data["統編"] = ", ".join(tax_ids[:1])
        else:
            data["統編"] = ""

        return data
    except:
        return {
            "公司名稱": "ERROR", 
            "電話": ", ".join(phones[:2]), 
            "Email": ", ".join(emails[:2]), 
            "傳真": faxes[0] if faxes else "",
            "統編": ", ".join(tax_ids[:1]),
            "網址": url
        }

# --- 6. 主程式 ---
keyword = st.text_input("🔍 請輸入搜尋關鍵字", value="廢水回收系統 公司")

if st.button("開始搜尋與分析"):
    if not gemini_api_key or not tavily_api_key:
        st.error("❌ 請輸入 API Key")
    else:
        genai.configure(api_key=gemini_api_key)
        model = genai.GenerativeModel('gemini-1.5-flash')
        tavily = TavilyClient(api_key=tavily_api_key)
        
        status_box = st.status("🚀 啟動全面追殺模式...", expanded=True)
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
                        tavily_snippet = item.get('content') or ""
                        
                        status_box.write(f"({i+1}/{len(search_results)}) 分析：{title}")
                        
                        # 1. 正常爬取
                        content, source_log = fetch_content_smart(url, fallback_content=tavily_raw)
                        data = extract_contact_info(content, url, model, snippet_content=tavily_snippet, company_name_hint=title)
                        
                        # 修復公司名稱
                        name = str(data.get("公司名稱", ""))
                        if name in ["ERROR", "None"] or "失敗" in name:
                            name = title
                            data["公司名稱"] = title

                        # 2. [全面補刀] 檢查所有關鍵欄位
                        missing_email = not data.get("Email") or str(data.get("Email")).lower() in ["none", "", "null"]
                        missing_phone = not data.get("電話") or str(data.get("電話")).lower() in ["none", "", "null"]
                        missing_fax = not data.get("傳真") or str(data.get("傳真")).lower() in ["none", "", "null"]

                        # 只要有缺，就發動搜尋
                        if missing_email or missing_phone or missing_fax:
                            missing_list = []
                            if missing_email: missing_list.append("Email")
                            if missing_phone: missing_list.append("電話")
                            if missing_fax: missing_list.append("傳真")
                            
                            if debug_mode: status_box.write(f"⚠️ {title} 缺少 {', '.join(missing_list)}，發動全面補刀...")
                            
                            # 補刀搜尋：一次問所有資訊
                            hunter_snippet = hunter_search(name, tavily)
                            
                            # 從補刀結果抓資料
                            new_emails, new_phones, new_faxes, new_tax_ids = regex_heavy_duty(hunter_snippet)
                            
                            # 回填資料 (只填本來空的)
                            if missing_email and new_emails:
                                data["Email"] = ", ".join(new_emails[:2])
                            if missing_phone and new_phones:
                                data["電話"] = ", ".join(new_phones[:2])
                            if missing_fax and new_faxes:
                                data["傳真"] = new_faxes[0]
                            # 統編也順便補一下
                            if not data.get("統編") and new_tax_ids:
                                data["統編"] = ", ".join(new_tax_ids[:1])

                            data["備註"] = "經補刀搜尋"

                        results_list.append(data)
                            
                    except Exception as e:
                        if debug_mode: st.warning(f"處理 {title} 時發生錯誤: {e}")
                        pass
                        
                    progress_bar.progress((i + 1) / len(search_results))
                    time.sleep(1)

                status_box.update(label="🎉 搜集完成！", state="complete", expanded=False)
                
                if results_list:
                    df = pd.DataFrame(results_list)
                    cols = ["公司名稱", "統編", "電話", "Email", "傳真", "網址", "備註"]
                    
                    for c in cols:
                        if c not in df.columns: df[c] = ""
                    df = df[cols]

                    st.dataframe(df)
                    excel_file = "leads_total_recall.xlsx"
                    df.to_excel(excel_file, index=False)
                    with open(excel_file, "rb") as f:
                        st.download_button("📥 下載 Excel 名單", f, file_name=f"{keyword}_全補完名單.xlsx")

        except Exception as e:
            st.error(f"發生錯誤：{e}")