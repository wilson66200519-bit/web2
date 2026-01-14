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
st.set_page_config(page_title="超級業務開發助手 (台灣終極修正版)", layout="wide")
st.title("🇹🇼 全自動客戶名單工廠 (台灣終極修正版)")
st.markdown("""
### 🛡️ 系統狀態：Ready
1. **網址強寫機制**：無論 AI 是否解析成功，強制寫入來源網址。
2. **名稱暴力清洗**：自動移除 SEO 贅字，還原乾淨公司名。
3. **統編分流**：8 碼數字自動歸類為統編，並過濾中國號碼。
4. **雙重備份**：優先使用即時爬蟲，失敗時調用搜尋引擎庫存。
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
    target_amount = st.slider("目標資料筆數", 10, 1000, 50, step=10)
    enable_hunter = st.toggle("開啟「補刀追殺」 (資料不全時自動二搜)", value=True)
    debug_mode = st.toggle("顯示除錯訊息", value=False)

# --- 3. 核心工具函數 ---

def get_root_url(url):
    """ 強制轉回首頁 """
    if not url: return ""
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}"
    except:
        return url

def force_clean_name(raw_title):
    """
    暴力清洗公司名稱：
    移除 "首頁", "Home", "公司簡介" 及分隔符號後的贅字
    """
    if not raw_title: return ""
    
    # 常見分隔符
    separators = ['|', '-', '_', ':', '–']
    best_candidate = raw_title
    
    for sep in separators:
        if sep in raw_title:
            parts = raw_title.split(sep)
            # 策略：找出長度最像公司名 (2~6字) 且包含 "公司/企業" 的片段
            found = False
            for p in parts:
                p = p.strip()
                if ("公司" in p or "商行" in p or "企業" in p) and len(p) < 20:
                    best_candidate = p
                    found = True
                    break
            # 如果沒找到明顯特徵，取最短但長度 > 1 的片段
            if not found:
                valid_parts = [p.strip() for p in parts if len(p.strip()) > 1]
                if valid_parts:
                    best_candidate = min(valid_parts, key=len)
            break 

    # 移除垃圾詞
    garbage = ["首頁", "Home", "Index", "歡迎光臨", "關於我們", "產品介紹", "聯絡我們", "系列", "廠商", "推薦", "有限公司"]
    # 注意：有限公司先不刪，保留完整性，最後再看情況
    
    cleaned = best_candidate
    for g in ["首頁", "Home", "Index"]: # 絕對垃圾詞
        cleaned = cleaned.replace(g, "")
        
    return cleaned.strip()

def fetch_content_robust(url, fallback_content=""):
    """ 強韌爬取流程：過濾中國網域 + Jina/Tavily 雙切換 """
    # 🚫 過濾非台灣網域
    if ".cn" in url or "china" in url.lower() or "alibaba" in url.lower():
        return "", "非台灣網域(過濾)"

    combined_content = ""
    source_log = []
    root_url = get_root_url(url)
    
    jina_url = f"https://r.jina.ai/{root_url}"
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        resp = requests.get(jina_url, headers=headers, timeout=8)
        
        # 簡單檢查簡體字
        if "联系我们" in resp.text: 
            pass 

        if resp.status_code == 200 and len(resp.text) > 100:
            combined_content += f"\n=== Jina即時爬取 ===\n{resp.text[:15000]}"
            source_log.append("即時爬蟲")
        else:
            raise Exception("Jina content too short")
            
    except Exception as e:
        # 失敗時使用備份
        if fallback_content and len(fallback_content) > 50:
            combined_content += f"\n=== 搜尋引擎庫存 ===\n{fallback_content[:15000]}"
            source_log.append("庫存救援")
        else:
            source_log.append("抓取失敗")

    return combined_content, " + ".join(source_log)

def regex_heavy_duty(text):
    """ 
    Regex 強力掃描：
    1. 嚴格區分 8 碼統編 vs 電話
    2. 過濾中國手機號
    """
    if not text: return [], [], [], []
    text_clean = " ".join(text.split())
    
    # Email
    emails = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', text_clean)
    all_emails = list(set(emails))

    # 傳真
    fax_patterns = [r'(?:Fax|FAX|傳真|F\.|F:)[\s:：\.]*(\(?0\d{1,2}\)?[\s\-]?[0-9-]{6,15})']
    faxes = []
    for pattern in fax_patterns:
        faxes.extend(re.findall(pattern, text))
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
            if clean_num in re.sub(r'\D', '', f): is_fax = True; break
        if is_fax: continue

        # 🚫 排除中國手機 (1開頭 11碼)
        if len(clean_num) == 11 and clean_num.startswith('1'): continue
        # 🚫 排除中國市話 (020, 021 開頭)
        if clean_num.startswith('020') or clean_num.startswith('021'): continue

        # ✅ 統編判斷：8碼，且不以 0 開頭
        if len(clean_num) == 8 and not clean_num.startswith('0'):
            tax_ids.append(clean_num)
        # ✅ 電話判斷：其餘長度
        elif len(clean_num) >= 8:
            phones.append(num)

    return all_emails, phones, faxes, tax_ids

def hunter_search(company_name, tavily_client):
    """ 補刀搜尋：加上 '台灣' """
    if not company_name or len(company_name) < 2: return ""
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
    """ Gemini AI 萃取 (不處理網址，網址由主程式寫入) """
    if "非台灣網域" in content:
        return {"公司名稱": force_clean_name(company_name_hint), "備註": "排除(非台灣網域)"}

    emails, phones, faxes, tax_ids = regex_heavy_duty(content)
    backup_info = f"預掃描 -> Email:{emails[:1]}, 電話:{phones[:1]}, 統編:{tax_ids[:1]}"
    
    clean_hint = force_clean_name(company_name_hint)

    # Prompt 不要求回傳網址
    prompt = f"""
    任務：資料標準化。
    目標公司：{clean_hint} (原始標題: {company_name_hint})
    參考數據：{backup_info}
    內容摘要：{content[:15000]}
    
    請回傳 JSON:
    {{
        "公司名稱": "請修正為正式全名 (去除 SEO 贅字)",
        "電話": "...", 
        "Email": "...",
        "傳真": "...",
        "統編": "...",
        "備註": "..."
    }}
    若找不到資料，請優先填入參考數據。
    """
    
    try:
        response = model.generate_content(prompt)
        txt = response.text.strip()
        if "```json" in txt: txt = txt.split("```json")[1].split("```")[0]
        elif "```" in txt: txt = txt.split("```")[0]
        data = json.loads(txt)
        
        # 強力回填 (AI 漏掉的用 Regex 補)
        if not data.get("Email") and emails: data["Email"] = emails[0]
        if not data.get("電話") and phones: data["電話"] = phones[0]
        if not data.get("傳真") and faxes: data["傳真"] = faxes[0]
        if not data.get("統編") and tax_ids: data["統編"] = tax_ids[0]
        
        # 二次清洗名稱
        if len(data.get("公司名稱", "")) > 15 or "-" in data.get("公司名稱", ""):
            data["公司名稱"] = force_clean_name(data["公司名稱"])
            
        return data
    except:
        status = "⚠️ AI失敗 (Regex救援)" if (phones or emails) else "❌ 解析失敗"
        return {
            "公司名稱": clean_hint,
            "電話": phones[0] if phones else "",
            "Email": emails[0] if emails else "",
            "傳真": faxes[0] if faxes else "",
            "統編": tax_ids[0] if tax_ids else "",
            "備註": status
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
    
    # 2. 搜集網址
    unique_data = {} 
    progress_bar = st.progress(0)
    status_box.write("🕸️ 正在過濾並搜集網址 (含庫存備份)...")
    
    for idx, q in enumerate(strategies):
        if len(unique_data) >= target_amount: break
        try:
            # include_raw_content=True 是防止空白的關鍵
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

            # AI 分析
            data = extract_contact_info(content, url, model, company_name_hint=title)
            
            # 🔥🔥🔥 物理強制寫入網址與來源 🔥🔥🔥
            data["網址"] = url
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
                if not data.get("統編") and h_tax: data["統編"] = h_tax[0]
                
                data["備註"] = "經二次補完"
            else:
                 if not data.get("備註"): data["備註"] = "一般"
            
            final_results.append(data)
            
            # 預覽表格
            if i % 3 == 0:
                df_show = pd.DataFrame(final_results)
                cols = ["公司名稱", "統編", "電話", "Email", "網址"]
                for c in cols: 
                    if c not in df_show.columns: df_show[c] = ""
                table_preview.dataframe(df_show[cols].tail(5))
                
        except Exception as e:
            if debug_mode: st.warning(f"Error on {title}: {e}")
            
        process_bar.progress((i+1)/len(target_list))
        time.sleep(0.5)

    # 4. 輸出
    status_box.update(label="🎉 完成！", state="complete", expanded=False)
    
    if final_results:
        df_final = pd.DataFrame(final_results)
        
        # 欄位整理
        target_cols = ["公司名稱", "統編", "電話", "Email", "傳真", "網址", "備註", "資料來源"]
        for c in target_cols:
            if c not in df_final.columns: df_final[c] = ""
        df_final = df_final[target_cols].astype(str)
        
        st.success(f"共產出 {len(df_final)} 筆台灣廠商名單")
        st.dataframe(df_final)
        
        # 輸出 CSV (UTF-8 BOM)
        csv = df_final.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 下載完整名單 (CSV)",
            data=csv,
            file_name="taiwan_leads_final.csv",
            mime="text/csv"
        )