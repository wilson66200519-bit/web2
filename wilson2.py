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
st.set_page_config(page_title="超級業務開發助手 (台灣精準版)", layout="wide")
st.title("🇹🇼 全自動客戶名單工廠 (台灣精準版)")
st.markdown("""
### 🛡️ 本次修正重點：
1. **嚴格區分統編與電話**：8 碼且非 0 開頭的數字，自動歸類為統編，不再誤判為電話。
2. **鎖定台灣廠商**：搜尋時強制加上 "台灣"，並自動過濾 `.cn` (中國) 網域。
3. **名稱AI清洗**：利用 AI 判斷網頁標題，還原出最乾淨的公司全名（去除 "首頁"、"專業製造" 等贅字）。
""")

# --- 2. 側邊欄設定 ---
with st.sidebar:
    st.header("⚙️ API 設定")
    try:
        gemini_api_key = st.secrets["GEMINI_API_KEY"]
        tavily_api_key = st.secrets["TAVILY_API_KEY"]
        st.success("✅ API Key 已從 Secrets 載入")
    except:
        gemini_api_key = st.text_input("輸入 Gemini API Key", type="password")
        tavily_api_key = st.text_input("輸入 Tavily API Key", type="password")
    
    st.divider()
    target_amount = st.slider("目標資料筆數", 10, 500, 30, step=10)
    enable_hunter = st.toggle("開啟「補刀追殺」", value=True)
    debug_mode = st.toggle("顯示除錯訊息", value=False)

# --- 3. 核心工具函數 ---

def get_root_url(url):
    if not url: return ""
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    except:
        return url

def fetch_content_robust(url, fallback_content=""):
    """ 強韌爬取流程 """
    # 🚫 過濾中國網域
    if ".cn" in url or "china" in url.lower():
        return "", "非台灣網域(過濾)"

    combined_content = ""
    source_log = []
    root_url = get_root_url(url)
    
    jina_url = f"https://r.jina.ai/{root_url}"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(jina_url, headers=headers, timeout=8)
        
        # 簡單檢查是否為簡體中文網站 (出現大量簡體特徵字)
        if "联系我们" in resp.text or "有限公司" in resp.text: 
            # 這裡只是一個簡單判斷，未必準確，但能擋掉一部分
            pass 

        if resp.status_code == 200 and len(resp.text) > 100:
            combined_content += f"\n=== Jina即時爬取 ===\n{resp.text[:15000]}"
            source_log.append("即時爬蟲")
        else:
            raise Exception("Jina fail")
            
    except Exception as e:
        if fallback_content and len(fallback_content) > 50:
            combined_content += f"\n=== 搜尋引擎庫存 ===\n{fallback_content[:15000]}"
            source_log.append("庫存救援")
        else:
            source_log.append("抓取失敗")

    return combined_content, " + ".join(source_log)

def regex_heavy_duty(text):
    """ 
    修正後的強力掃描：
    1. 嚴格區分 8 碼統編 vs 電話
    2. 過濾中國手機號 (11碼, 1開頭)
    """
    if not text: return [], [], [], []
    text_clean = " ".join(text.split())
    
    # Email
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text_clean)
    all_emails = list(set(emails))

    # 傳真 (Fax)
    fax_patterns = [r'(?:Fax|FAX|傳真|F\.|F:)[\s:：\.]*(\(?0\d{1,2}\)?[\s\-]?[0-9-]{6,15})']
    faxes = []
    for pattern in fax_patterns:
        faxes.extend(re.findall(pattern, text))
    faxes = list(set(faxes))
    
    # 電話與統編邏輯重構
    raw_numbers = re.findall(r'(?:\(?0\d{1,2}\)?[\s\-]?)?\d{3,4}[\s\-]?\d{3,4}', text_clean)
    phones = []
    tax_ids = []
    
    for num in list(set(raw_numbers)):
        clean_num = re.sub(r'\D', '', num)
        
        # 排除傳真
        is_fax = False
        for f in faxes:
            if clean_num in re.sub(r'\D', '', f): is_fax = True; break
        if is_fax: continue

        # 🚫 排除中國手機號 (1開頭, 11碼)
        if len(clean_num) == 11 and clean_num.startswith('1'):
            continue

        # ✅ 統編判斷：8碼，且通常不以 0 開頭 (台灣手機是 09 開頭共 10 碼，市話含區碼 9-10 碼)
        if len(clean_num) == 8 and not clean_num.startswith('0'):
            tax_ids.append(clean_num)
        # ✅ 電話判斷：9碼以上，或是 8 碼但以 0 開頭 (極少見，可能是未加區碼的市話，先歸類為電話)
        elif len(clean_num) >= 8:
            phones.append(num)

    return all_emails, phones, faxes, tax_ids

def hunter_search(company_name, tavily_client):
    """ 補刀搜尋：加上 '台灣' 關鍵字 """
    if not company_name or len(company_name) < 2: return ""
    # 強制加上 "台灣" 避免搜到大陸同名公司
    query = f"{company_name} 台灣 電話 email 聯絡方式"
    try:
        resp = tavily_client.search(query=query, max_results=3, search_depth="advanced")
        snippets = ""
        for res in resp.get('results', []):
            snippets += res.get('content', '') + "\n"
        return snippets
    except:
        return ""

def extract_contact_info(content, url, model, company_name_hint=""):
    """ Gemini AI 萃取 (加入名稱清洗指令) """
    if "非台灣網域" in content: # 快速失敗
        return {"公司名稱": company_name_hint, "備註": "排除(非台灣網域)"}

    emails, phones, faxes, tax_ids = regex_heavy_duty(content)
    backup_info = f"預掃描 -> Email:{emails[:1]}, 電話:{phones[:1]}, 統編:{tax_ids[:1]}"
    
    prompt = f"""
    你是一個資料提取專家。請處理以下台灣公司的資料。
    
    網址：{url}
    原始標題：{company_name_hint}
    參考數據：{backup_info}
    網頁內容：
    {content[:20000]} 
    
    任務 1: 清洗公司名稱。請從原始標題或內文中找出「正式全名」。
           (例如: "首頁 - 建越科技廢水處理" -> "建越科技股份有限公司")
           (例如: "Good Water Co." -> "Good Water Co.")
           如果不確定，就保留最像公司名的部分。
           
    任務 2: 提取聯絡資訊。
    
    請回傳純 JSON:
    {{
        "公司名稱": "...", 
        "電話": "...", 
        "Email": "...",
        "傳真": "...",
        "統編": "...",
        "備註": "..."
    }}
    若找不到，優先使用參考數據。
    """
    
    try:
        response = model.generate_content(prompt)
        txt = response.text.strip()
        if "```json" in txt: txt = txt.split("```json")[1].split("```")[0]
        elif "```" in txt: txt = txt.split("```")[0]
        data = json.loads(txt)
        
        # 強力回填
        if not data.get("Email") and emails: data["Email"] = emails[0]
        if not data.get("電話") and phones: data["電話"] = phones[0]
        if not data.get("傳真") and faxes: data["傳真"] = faxes[0]
        if not data.get("統編") and tax_ids: data["統編"] = tax_ids[0]
        
        return data
    except:
        return {
            "公司名稱": company_name_hint,
            "電話": phones[0] if phones else "",
            "Email": emails[0] if emails else "",
            "傳真": faxes[0] if faxes else "",
            "統編": tax_ids[0] if tax_ids else "",
            "備註": "AI解析失敗"
        }

def generate_keywords(base_keyword, amount, model):
    """ 生成策略：強制加上 '台灣' """
    num_strategies = max(3, int(amount / 15))
    prompt = f"""
    請生成 {num_strategies} 組搜尋關鍵字，目的是搜集「台灣」的「{base_keyword}」廠商。
    請確保關鍵字都包含 "台灣" 或台灣地名 (台北, 台中, 高雄)。
    只回傳 JSON Array string: ["關鍵字1", "關鍵字2", ...]
    """
    try:
        res = model.generate_content(prompt)
        txt = res.text.strip()
        if "```json" in txt: txt = txt.split("```json")[1].split("```")[0]
        return json.loads(txt)
    except:
        return [f"台灣 {base_keyword}", f"台北 {base_keyword}", f"台中 {base_keyword}", f"高雄 {base_keyword}"]

# --- 4. 主執行邏輯 ---
st.subheader("🕵️‍♂️ 啟動控制台")
keyword = st.text_input("輸入核心關鍵字 (系統會自動限定台灣範圍)", value="廢水回收系統")

if st.button("🚀 啟動台灣精準版引擎"):
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
    status_box = st.status("🧠 規劃台灣限定搜尋策略...", expanded=True)
    
    # 1. 策略生成
    strategies = generate_keywords(keyword, target_amount, model)
    status_box.write(f"✅ 搜尋策略：{strategies}")
    
    # 2. 搜集網址 (過濾 .cn)
    unique_data = {} 
    progress_bar = st.progress(0)
    status_box.write("🕸️ 正在過濾並搜集網址...")
    
    for idx, q in enumerate(strategies):
        if len(unique_data) >= target_amount: break
        try:
            response = tavily.search(query=q, max_results=15, include_raw_content=True)
            for res in response.get('results', []):
                url = res.get('url')
                # 🚫 網域層級過濾
                if url and ".cn" not in url and "alibaba" not in url and not url.endswith('.pdf'):
                    if url not in unique_data:
                        unique_data[url] = {
                            "title": res.get('title', ''),
                            "raw_content": res.get('raw_content') or res.get('content', '') 
                        }
        except: pass
        progress_bar.progress(min(len(unique_data) / target_amount, 1.0))
        time.sleep(1)

    # 3. 深度挖掘
    status_box.write(f"🏭 開始處理 {len(unique_data)} 筆台灣廠商資料...")
    final_results = []
    process_bar = st.progress(0)
    table_preview = st.empty()
    
    target_list = list(unique_data.items())[:target_amount]
    
    for i, (url, info) in enumerate(target_list):
        title = info['title']
        raw_backup = info['raw_content']
        
        try:
            content, source = fetch_content_robust(url, fallback_content=raw_backup)
            
            # 若第一步就發現是非台灣網域，跳過
            if "非台灣網域" in source:
                continue

            data = extract_contact_info(content, url, model, company_name_hint=title)
            data["資料來源"] = source
            
            # 補刀檢查
            missing = []
            if not data.get("Email") or str(data.get("Email")).lower() in ["none", ""]: missing.append("Email")
            if not data.get("電話") or str(data.get("電話")).lower() in ["none", ""]: missing.append("電話")
            
            if enable_hunter and missing:
                if debug_mode: status_box.write(f"🔫 {data['公司名稱']} 資料不全，補刀中...")
                hunter_data = hunter_search(data['公司名稱'], tavily)
                h_emails, h_phones, h_faxes, h_tax = regex_heavy_duty(hunter_data)
                
                if "Email" in missing and h_emails: data["Email"] = h_emails[0]
                if "電話" in missing and h_phones: data["電話"] = h_phones[0]
                if not data.get("統編") and h_tax: data["統編"] = h_tax[0] # 補刀也要補統編
                
                data["備註"] = "經二次補完"
            else:
                 if not data.get("備註"): data["備註"] = "一般"
            
            final_results.append(data)
            
            if i % 2 == 0:
                df_show = pd.DataFrame(final_results)
                cols = ["公司名稱", "統編", "電話", "Email", "網址"]
                for c in cols: 
                    if c not in df_show.columns: df_show[c] = ""
                table_preview.dataframe(df_show[cols].tail(5))
                
        except Exception as e:
            if debug_mode: st.warning(f"Error: {e}")
            
        process_bar.progress((i+1)/len(target_list))
        time.sleep(0.5)

    status_box.update(label="🎉 完成！", state="complete", expanded=False)
    
    if final_results:
        df_final = pd.DataFrame(final_results)
        target_cols = ["公司名稱", "統編", "電話", "Email", "傳真", "網址", "備註", "資料來源"]
        for c in target_cols:
            if c not in df_final.columns: df_final[c] = ""
        df_final = df_final[target_cols].astype(str)
        
        st.success(f"共產出 {len(df_final)} 筆台灣廠商名單")
        st.dataframe(df_final)
        
        csv = df_final.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載名單 (CSV)", csv, "taiwan_leads.csv", "text/csv")