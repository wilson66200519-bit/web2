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
st.set_page_config(page_title="超級業務開發助手 (最終量產版)", layout="wide")
st.title("🏭 全自動客戶名單工廠 (最終量產版)")
st.markdown("""
### 🛡️ 系統就緒：
1. **雙重資料源**：優先使用 Jina 即時爬蟲，失敗時自動切換至 Tavily 搜尋庫存。
2. **格式保證**：輸出 **CSV (UTF-8 BOM)**，Excel 開啟不亂碼，無需額外套件。
3. **長效執行**：優化記憶體與速率限制，適合執行 500+ 筆的大型任務。
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ API 設定")
    
    # 優先讀取 Secrets，若無則顯示輸入框
    try:
        gemini_api_key = st.secrets["GEMINI_API_KEY"]
        tavily_api_key = st.secrets["TAVILY_API_KEY"]
        st.success("✅ API Key 已從 Secrets 載入")
    except:
        gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")
        tavily_api_key = st.text_input("輸入 Tavily API Key", type="password")
    
    st.divider()
    st.header("🎯 目標設定")
    target_amount = st.slider("目標資料筆數", 10, 1000, 50, step=10)
    enable_hunter = st.toggle("開啟「補刀追殺」 (缺資料時自動搜第二次)", value=True)
    debug_mode = st.toggle("顯示除錯訊息", value=False)

# --- 3. 核心工具函數 ---

def get_root_url(url):
    """ 強制轉回首頁，提高聯絡資訊命中率 """
    if not url: return ""
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    except:
        return url

def fetch_content_robust(url, fallback_content=""):
    """ 
    強韌的爬取流程：
    1. 嘗試 Jina AI (閱讀模式)
    2. 失敗則使用 Tavily 的庫存 (fallback_content)
    """
    combined_content = ""
    source_log = []
    root_url = get_root_url(url)
    
    # 嘗試 1: Jina AI
    jina_url = f"https://r.jina.ai/{root_url}"
    try:
        # 設定 User-Agent 避免被某些網站直接擋
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(jina_url, headers=headers, timeout=8)
        
        if resp.status_code == 200 and len(resp.text) > 100:
            combined_content += f"\n=== Jina即時爬取 ===\n{resp.text[:15000]}"
            source_log.append("即時爬蟲")
        else:
            raise Exception("Jina content too short or blocked")
            
    except Exception as e:
        # 失敗時使用備份 (Tavily Raw Content) - 這是防止空白的關鍵
        if fallback_content and len(fallback_content) > 50:
            combined_content += f"\n=== 搜尋引擎庫存 ===\n{fallback_content[:15000]}"
            source_log.append("庫存救援")
        else:
            source_log.append("抓取失敗")

    return combined_content, " + ".join(source_log)

def regex_heavy_duty(text):
    """ 正則表達式強力掃描 (電話、Email、傳真、統編) """
    if not text: return [], [], [], []
    
    # 移除過多空白
    text_clean = " ".join(text.split())
    
    # Email
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text_clean)
    mailto_emails = re.findall(r'mailto:([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', text)
    all_emails = list(set(emails + mailto_emails))

    # 傳真 (Fax) 關鍵字偵測
    fax_patterns = [
        r'(?:Fax|FAX|傳真|F\.|F:)[\s:：\.]*(\(?0\d{1,2}\)?[\s\-]?[0-9-]{6,15})'
    ]
    faxes = []
    for pattern in fax_patterns:
        found = re.findall(pattern, text)
        faxes.extend(found)
    faxes = list(set(faxes))
    
    # 電話與統編
    raw_numbers = re.findall(r'(?:\(?0\d{1,2}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}', text_clean)
    phones = []
    tax_ids = []
    
    for num in list(set(raw_numbers)):
        clean_num = re.sub(r'\D', '', num)
        # 排除傳真
        is_fax = False
        for f in faxes:
            if clean_num in re.sub(r'\D', '', f):
                is_fax = True; break
        if is_fax: continue

        if len(clean_num) == 8 and not clean_num.startswith('0'):
            tax_ids.append(clean_num)
        elif len(clean_num) >= 8:
            phones.append(num)

    return all_emails, phones, faxes, tax_ids

def hunter_search(company_name, tavily_client):
    """ 補刀搜尋：針對特定公司找聯絡方式 """
    if not company_name or len(company_name) < 2: return ""
    query = f"{company_name} 台灣 電話 email 聯絡方式 contact"
    try:
        resp = tavily_client.search(query=query, max_results=3, search_depth="advanced")
        snippets = ""
        for res in resp.get('results', []):
            snippets += res.get('content', '') + "\n"
        return snippets
    except:
        return ""

def extract_contact_info(content, url, model, company_name_hint=""):
    """ Gemini AI 萃取 """
    # 1. 先用 Regex 掃一遍，確保 AI 失敗時有保底資料
    emails, phones, faxes, tax_ids = regex_heavy_duty(content)
    backup_info = f"預掃描 -> Email:{emails[:1]}, 電話:{phones[:1]}"
    
    # 2. 建構 Prompt
    prompt = f"""
    你是一個精準的資料提取專家。
    目標：找出 "{company_name_hint}" 的聯絡資訊。
    網址：{url}
    參考(正則預掃)：{backup_info}
    
    網頁內容摘要：
    {content[:25000]} 
    
    請回傳純 JSON 格式 (不要 Markdown)：
    {{
        "公司名稱": "{company_name_hint}", 
        "電話": "...", 
        "Email": "...",
        "傳真": "...",
        "統編": "...",
        "備註": "..."
    }}
    注意：若找不到，請優先使用「參考」中的數據。若都無則留空。
    """
    
    try:
        response = model.generate_content(prompt)
        txt = response.text.strip()
        # 清洗 JSON 標記
        if "```json" in txt: txt = txt.split("```json")[1].split("```")[0]
        elif "```" in txt: txt = txt.split("```")[0]
        data = json.loads(txt)
        
        # 3. 強力回填 (如果 AI 漏抓，強制補上 Regex 抓到的)
        if not data.get("Email") and emails: data["Email"] = emails[0]
        if not data.get("電話") and phones: data["電話"] = phones[0]
        if not data.get("傳真") and faxes: data["傳真"] = faxes[0]
        if not data.get("統編") and tax_ids: data["統編"] = tax_ids[0]
        
        return data
    except:
        # 4. 萬一 AI 完全崩潰，回傳 Regex 抓到的基本資料
        return {
            "公司名稱": company_name_hint,
            "電話": phones[0] if phones else "",
            "Email": emails[0] if emails else "",
            "傳真": faxes[0] if faxes else "",
            "統編": tax_ids[0] if tax_ids else "",
            "備註": "AI失敗，僅正則抓取"
        }

def generate_keywords(base_keyword, amount, model):
    """ 關鍵字裂變策略 """
    num_strategies = max(3, int(amount / 15)) # 估計每個關鍵字能抓 15 筆不重複的
    prompt = f"""
    請生成 {num_strategies} 組搜尋關鍵字，目的是搜集「{base_keyword}」相關的台灣公司名單。
    請包含：地區變體 (如 {base_keyword} 台中)、應用變體 (如 工業{base_keyword})、長尾詞。
    只回傳 JSON Array string，例如：["關鍵字1", "關鍵字2", ...]
    """
    try:
        res = model.generate_content(prompt)
        txt = res.text.strip()
        if "```json" in txt: txt = txt.split("```json")[1].split("```")[0]
        return json.loads(txt)
    except:
        # 備用策略
        return [f"{base_keyword} {city}" for city in ["台北", "桃園", "新竹", "台中", "台南", "高雄", "廠商", "工程", "設備"]]

# --- 4. 主執行邏輯 ---
st.subheader("🕵️‍♂️ 啟動控制台")
keyword = st.text_input("輸入核心關鍵字", value="廢水回收系統")

if st.button("🚀 啟動量產引擎"):
    if not gemini_api_key or not tavily_api_key:
        st.error("❌ 請填寫 API Key")
        st.stop()
        
    # 初始化
    genai.configure(api_key=gemini_api_key)
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        model.generate_content("test")
    except:
        model = genai.GenerativeModel('gemini-pro')
        
    tavily = TavilyClient(api_key=tavily_api_key)
    
    # 狀態顯示
    status_box = st.status("🧠 AI 戰略規劃中...", expanded=True)
    
    # === 階段 1. 關鍵字裂變 ===
    strategies = generate_keywords(keyword, target_amount, model)
    status_box.write(f"✅ 策略生成：將使用 {len(strategies)} 組關鍵字進行地毯式搜索。")
    
    # === 階段 2. 建立網址池 (URL Pool) ===
    unique_data = {} # 用來存 {url: {title, raw_content}}
    progress_bar = st.progress(0)
    
    status_box.write("🕸️ 正在撒網捕撈網址 (含庫存頁面備份)...")
    
    for idx, q in enumerate(strategies):
        if len(unique_data) >= target_amount: break
        
        try:
            # 關鍵修正：include_raw_content=True 是資料庫存的核心
            response = tavily.search(query=q, max_results=15, include_raw_content=True)
            
            for res in response.get('results', []):
                url = res.get('url')
                if url and url not in unique_data:
                    if not url.endswith('.pdf'): # 排除 PDF
                        unique_data[url] = {
                            "title": res.get('title', ''),
                            # 優先使用 raw_content，若無則用 content
                            "raw_content": res.get('raw_content') or res.get('content', '') 
                        }
        except Exception as e:
            if debug_mode: st.warning(f"搜尋 {q} 時略過: {e}")
            pass
            
        progress_bar.progress(min(len(unique_data) / target_amount, 1.0))
        status_box.write(f"🔍 目前庫存：{len(unique_data)} 筆 (正在搜尋: {q})")
        time.sleep(1) # 避免過快

    # === 階段 3. 深度挖掘 ===
    status_box.write(f"🏭 網址搜集完畢 (共{len(unique_data)}筆)，開始進行深度加工與補完...")
    
    final_results = []
    process_bar = st.progress(0)
    table_preview = st.empty()
    
    target_list = list(unique_data.items())[:target_amount]
    
    for i, (url, info) in enumerate(target_list):
        title = info['title']
        raw_backup = info['raw_content']
        
        status_box.write(f"🔨 ({i+1}/{len(target_list)}) 加工中：{title}")
        
        try:
            # A. 抓取內容 (優先 Jina -> 失敗用庫存 raw_backup)
            content, source = fetch_content_robust(url, fallback_content=raw_backup)
            
            # B. AI 提取
            data = extract_contact_info(content, url, model, company_name_hint=title)
            data["資料來源"] = source
            
            # C. 補刀機制 (Hunter Mode)
            missing = []
            if not data.get("Email") or str(data.get("Email")).lower() in ["none", ""]: missing.append("Email")
            if not data.get("電話") or str(data.get("電話")).lower() in ["none", ""]: missing.append("電話")
            
            if enable_hunter and missing:
                if debug_mode: status_box.write(f"🔫 {title} 資料不全，發動補刀...")
                hunter_data = hunter_search(title, tavily)
                
                # 從補刀資料中再次提取
                h_emails, h_phones, h_faxes, h_tax = regex_heavy_duty(hunter_data)
                
                if "Email" in missing and h_emails: data["Email"] = h_emails[0]
                if "電話" in missing and h_phones: data["電話"] = h_phones[0]
                if not data.get("傳真") and h_faxes: data["傳真"] = h_faxes[0]
                
                data["備註"] = "經二次補完"
            else:
                 if not data.get("備註"): data["備註"] = "一般"
            
            final_results.append(data)
            
            # 預覽更新
            if i % 3 == 0:
                df_show = pd.DataFrame(final_results)
                cols = ["公司名稱", "電話", "Email", "傳真", "網址", "備註"]
                for c in cols: 
                    if c not in df_show.columns: df_show[c] = ""
                table_preview.dataframe(df_show[cols].tail(5))
                
        except Exception as e:
            if debug_mode: st.warning(f"Error on {title}: {e}")
            
        process_bar.progress((i+1)/len(target_list))
        time.sleep(0.5)

    # === 階段 4. 輸出 ===
    status_box.update(label="🎉 任務完成！", state="complete", expanded=False)
    
    if final_results:
        df_final = pd.DataFrame(final_results)
        
        # 欄位整理
        target_cols = ["公司名稱", "電話", "Email", "傳真", "統編", "網址", "備註", "資料來源"]
        for c in target_cols:
            if c not in df_final.columns: df_final[c] = ""
        df_final = df_final[target_cols]
        
        # 強制轉字串，避免 CSV 電話掉 0
        df_final = df_final.astype(str)
        
        st.success(f"共產出 {len(df_final)} 筆有效名單")
        st.dataframe(df_final)
        
        # CSV 下載 (Excel 開啟不亂碼的關鍵：utf-8-sig)
        csv = df_final.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載 Excel 格式 (CSV)",
            data=csv,
            file_name="leads_production.csv",
            mime="text/csv"
        )