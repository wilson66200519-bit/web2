import streamlit as st
import google.generativeai as genai
import pandas as pd
import json
import time
import requests
import re
from tavily import TavilyClient

# --- 1. 頁面設定 ---
st.set_page_config(page_title="精準客戶名單搜集器 (美觀版)", layout="wide")
st.title("🎯 精準客戶名單搜集器 (完美顯示版)")
st.markdown("""
### ✨ 介面與功能升級：
1. **表格美化**：網址自動縮短為「🔗 前往官網」，不再佔用大量版面。
2. **格式分離**：網頁上看得到的電話很乾淨，下載的 Excel 依然有防呆保護。
3. **雙重備援**：爬蟲失敗時自動使用搜尋庫存，防止資料空白。
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ API 設定")
    try:
        gemini_api_key = st.secrets["GEMINI_API_KEY"]
        tavily_api_key = st.secrets["TAVILY_API_KEY"]
        st.success("✅ API Key 已載入")
    except:
        gemini_api_key = st.text_input("Gemini API Key", type="password")
        tavily_api_key = st.text_input("Tavily API Key", type="password")
    
    st.divider()
    st.header("🎯 搜尋設定")
    target_amount = st.slider("目標有效筆數", 50, 200, 50, step=10)
    strict_mode = st.checkbox("嚴格模式 (電話或Email至少要有一個)", value=True)

# --- 3. 核心工具函數 ---

def is_junk_link(url, title):
    """ 排除非目標網站 """
    url = url.lower()
    title = title.lower()
    
    bad_domains = [
        '.gov', '.edu', 'facebook', 'youtube', 'instagram', 'wiki', 'blog', 
        'news', 'ptt.cc', 'dcard', '104.com', '1111.com', '518.com', 'linkedin',
        'tw.yahoo.com', 'google.com'
    ]
    bad_keywords = [
        '新聞', '報導', '日報', '懶人包', '公告', '標案', '政府', '補助', 
        '論文', '研究', 'pdf', 'doc', '下載', '名錄', '清冊', 
        '徵才', '職缺', '招聘', 'job', 'hiring', 'career'
    ]
    
    for d in bad_domains:
        if d in url: return True
    for k in bad_keywords:
        if k in title: return True
        
    return False

def clean_text(text):
    """ 清洗文字 """
    if not text: return ""
    text = str(text)
    text = text.replace('\n', ' ').replace('\r', '')
    text = re.sub(r'\s+', ' ', text)
    if text.lower() in ['none', 'null', 'unknown', '無']:
        return ""
    return text.strip()

def regex_scan(text):
    """ 正則掃描：電話、Email、傳真 """
    if not text: return [], [], []
    
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text)
    
    fax_patterns = [r'(?:Fax|FAX|傳真|F\.|F:)[\s:：\.]*(\(?0\d{1,2}\)?[\s\-]?[0-9-]{6,15})']
    faxes = []
    for pattern in fax_patterns:
        faxes.extend(re.findall(pattern, text))

    raw_phones = re.findall(r'(?:\(?0\d{1,2}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}', text)
    valid_phones = []
    
    for p in raw_phones:
        clean_p = re.sub(r'\D', '', p)
        is_fax = False
        for f in faxes:
            if clean_p in re.sub(r'\D', '', f):
                is_fax = True; break
        if is_fax: continue

        if len(clean_p) >= 8 and not clean_p.startswith('202'):
            valid_phones.append(p)
            
    return list(set(emails)), list(set(valid_phones)), list(set(faxes))

def fetch_and_extract(url, title, fallback_content, model):
    """ 抓取網頁並提取資料 """
    content = ""
    try:
        jina_url = f"https://r.jina.ai/{url}"
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(jina_url, headers=headers, timeout=8)
        if resp.status_code == 200 and len(resp.text) > 100:
            content = resp.text[:15000] 
        else:
            raise Exception("Jina failed")
    except:
        content = fallback_content[:15000] if fallback_content else ""

    emails, phones, faxes = regex_scan(content)
    backup_email = emails[0] if emails else ""
    backup_phone = phones[0] if phones else ""
    backup_fax = faxes[0] if faxes else ""
    
    prompt = f"""
    你是一個資料整理助手。請從網頁內容中提取 "{title}" 的聯絡資訊。
    參考資料：Email={backup_email}, 電話={backup_phone}, 傳真={backup_fax}
    網頁內容：
    {content}
    
    請回傳純 JSON：
    {{
        "公司名稱": "請精簡公司全名",
        "電話": "...",
        "Email": "...",
        "傳真": "..."
    }}
    若找不到，請優先使用參考資料。
    """
    
    try:
        res = model.generate_content(prompt)
        txt = res.text.strip()
        if "```json" in txt: txt = txt.split("```json")[1].split("```")[0]
        elif "```" in txt: txt = txt.split("```")[0]
        data = json.loads(txt)
    except:
        data = {"公司名稱": title, "電話": backup_phone, "Email": backup_email, "傳真": backup_fax}
        
    if not data.get("Email") and backup_email: data["Email"] = backup_email
    if not data.get("電話") and backup_phone: data["電話"] = backup_phone
    if not data.get("傳真") and backup_fax: data["傳真"] = backup_fax
    
    data["網址"] = url
    return data

# --- 4. 主程式邏輯 ---
keyword = st.text_input("🔍 輸入關鍵字", value="廢水回收系統")

if st.button("🚀 開始搜集"):
    if not gemini_api_key or not tavily_api_key:
        st.error("❌ 請填寫 API Key")
        st.stop()
        
    genai.configure(api_key=gemini_api_key)
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        model.generate_content("test")
    except:
        model = genai.GenerativeModel('gemini-pro')
    
    tavily = TavilyClient(api_key=tavily_api_key)
    
    status = st.status("正在過濾並建立名單...", expanded=True)
    
    # 1. 建立網址池
    unique_data = {} 
    search_queries = [
        f"{keyword} 廠商", f"{keyword} 公司", f"{keyword} 供應商", 
        f"{keyword} 工程", f"{keyword} 設備", f"{keyword} 聯繫方式"
    ]
    
    progress = st.progress(0)
    
    for q in search_queries:
        if len(unique_data) >= target_amount * 1.5: 
            break
        try:
            resp = tavily.search(query=q, max_results=15, include_raw_content=True)
            for res in resp.get('results', []):
                url = res.get('url')
                title = res.get('title')
                raw = res.get('raw_content') or res.get('content')
                
                if url and title and url not in unique_data:
                    if not is_junk_link(url, title):
                        unique_data[url] = {"title": title, "raw": raw}
        except: pass
        
        status.write(f"🔍 已找到 {len(unique_data)} 個潛在目標 (過濾雜訊後)...")
        time.sleep(1)
        
    status.write(f"✅ 網址搜集完成，共 {len(unique_data)} 筆。開始深度挖掘...")
    
    # 2. 深度挖掘
    final_data = [] # 儲存原始乾淨資料 (給網頁顯示用)
    target_list = list(unique_data.items())
    
    table_placeholder = st.empty()
    
    for i, (url, info) in enumerate(target_list):
        if len(final_data) >= target_amount:
            break
            
        title = info['title']
        raw_backup = info['raw']
        status.write(f"🔨 ({i+1}/{len(target_list)}) 處理中：{title}")
        
        data = fetch_and_extract(url, title, raw_backup, model)
        
        if data:
            name = clean_text(data.get("公司名稱", title))
            phone = clean_text(data.get("電話", ""))
            email = clean_text(data.get("Email", ""))
            fax = clean_text(data.get("傳真", ""))
            link = str(data.get("網址", url))
            
            has_contact = (len(phone) > 5) or ('@' in email)
            if strict_mode and not has_contact:
                status.write(f"⚠️ {title} 無有效聯絡資訊，剔除。")
                continue 
            
            # 這裡只存原始資料，不要加單引號
            row = {
                "公司名稱": name,
                "電話": phone,
                "Email": email,
                "傳真": fax,
                "網址": link
            }
            final_data.append(row)
            
            # 即時預覽 (使用 Column Config 美化)
            df_preview = pd.DataFrame(final_data)
            table_placeholder.dataframe(
                df_preview.tail(3),
                column_config={
                    "網址": st.column_config.LinkColumn("網址", display_text="🔗 前往官網"),
                    "Email": st.column_config.TextColumn("Email"),
                },
                use_container_width=True,
                hide_index=True
            )
        
        progress.progress(min(len(final_data) / target_amount, 1.0))
        time.sleep(0.5)

    # 3. 輸出結果
    status.update(label="🎉 完成！", state="complete", expanded=False)
    
    if final_data:
        df = pd.DataFrame(final_data)
        
        cols = ["公司名稱", "電話", "Email", "傳真", "網址"]
        df = df[cols]
        
        st.success(f"成功搜集 {len(df)} 筆有效名單！")
        
        # === 顯示美化表格 (網頁版) ===
        st.dataframe(
            df,
            column_config={
                "網址": st.column_config.LinkColumn("官方網站", display_text="🔗 前往官網"),
                "電話": st.column_config.TextColumn("電話號碼"),
                "Email": st.column_config.TextColumn("Email 信箱"),
                "傳真": st.column_config.TextColumn("傳真號碼"),
            },
            use_container_width=True,
            hide_index=True
        )
        
        # === 準備下載檔案 (Excel版) ===
        # 在這裡才加上單引號，讓 Excel 不掉 0
        df_download = df.copy()
        df_download["電話"] = df_download["電話"].apply(lambda x: f"'{x}" if x and str(x).startswith('0') else x)
        df_download["傳真"] = df_download["傳真"].apply(lambda x: f"'{x}" if x and str(x).startswith('0') else x)
        
        csv = df_download.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            "📥 下載 Excel 格式 (.csv)",
            csv,
            "company_list_pro.csv",
            "text/csv",
            type="primary"
        )
    else:
        st.warning("找不到符合條件的資料，請嘗試更換關鍵字。")