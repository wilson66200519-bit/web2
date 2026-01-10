import streamlit as st
import pandas as pd
from tavily import TavilyClient
import google.generativeai as genai
import time
import json
import re
import concurrent.futures
import random
import io # 用於處理 Excel 記憶體寫入
import xlsxwriter # 用於 Excel 美化

# ==========================================
# 🔑 設定 API Key (優先讀取 Secrets)
# ==========================================
try:
    tavily_api_key = st.secrets["TAVILY_API_KEY"]
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
    api_source = "Secrets"
except:
    tavily_api_key = ""
    gemini_api_key = ""
    api_source = "None"

# --- 1. 基礎設定 ---
st.set_page_config(page_title="企業名單搜集器 (Pro)", layout="wide")
st.title("⚡ 企業名單搜集 (極速美化版)")
st.markdown("""
**此版本集大成之作：**
1. 🚀 **極速引擎**：多執行緒併發，速度提升 5 倍。
2. 🧹 **智能清洗**：自動修正電話格式、過濾無效 Email。
3. 🎨 **Excel 美化**：輸出原生 `.xlsx` 檔，自動調整欄寬、標題加粗上色。
""")

# --- 2. 側邊欄 ---
with st.sidebar:
    st.header("⚙️ 參數設定")
    if not tavily_api_key:
        tavily_api_key = st.text_input("Tavily API Key", type="password")
    if not gemini_api_key:
        gemini_api_key = st.text_input("Gemini API Key", type="password")
        
    st.divider()
    target_limit = st.slider("🎯 目標筆數", 10, 200, 50, 10)
    max_workers = st.slider("⚡ 同時搜尋線程數", 1, 10, 5, help="建議設為 3-5，太高可能會被 API 限制速率")

# --- Helper: 資料清洗 ---
def clean_phone(phone_str):
    """只保留數字與相關符號"""
    if not phone_str: return ""
    # 移除中文、英文字母，只留 0-9, +, -, (, ), #, 空格
    cleaned = re.sub(r'[^\d\+\-\(\)\#\s]', '', str(phone_str))
    return cleaned.strip()

def validate_email(email_str):
    """移除無效 Email"""
    if not email_str: return ""
    email_str = str(email_str).strip()
    # 簡單驗證是否包含 @ 和 .
    if re.match(r"[^@]+@[^@]+\.[^@]+", email_str):
        return email_str
    return ""

# --- Helper: AI 呼叫 (含重試機制) ---
def robust_gemini_call(model, prompt, max_retries=3):
    wait_time = 2
    for attempt in range(max_retries):
        try:
            response = model.generate_content(prompt)
            return response.text
        except Exception as e:
            if "429" in str(e): # Rate limit
                time.sleep(wait_time + random.random())
                wait_time *= 2
            else:
                return None
    return None

# --- Worker: 單一公司處理邏輯 ---
def process_single_company(company, tavily_client, model):
    """背景執行的單一任務"""
    # 名字清洗 (移除後綴)
    clean_name = re.split(r'[-|–_]', company['公司名稱'])[0].strip()
    
    # 隨機延遲避免併發衝撞
    time.sleep(random.uniform(0.1, 1.0))
    
    try:
        # 1. 深度搜尋
        query = f"{clean_name} 聯絡電話 email"
        search_res = tavily_client.search(query=query, max_results=3, search_depth="advanced")
        context = "\n".join([r['content'] for r in search_res.get('results', [])])
        
        # 2. AI 萃取
        prompt = f"""
        找出 "{clean_name}" 的聯絡資料。
        參考資料：
        {context[:1500]}
        
        請回傳純 JSON: {{"電話": "", "Email": "", "傳真": ""}}
        找不到留空。不要 Markdown。
        """
        
        ai_text = robust_gemini_call(model, prompt)
        
        if ai_text:
            clean_json = ai_text.replace("```json", "").replace("```", "").strip()
            info = json.loads(clean_json)
            
            # 3. 寫入並清洗
            company['電話'] = clean_phone(info.get('電話', ''))
            company['Email'] = validate_email(info.get('Email', ''))
            company['傳真'] = clean_phone(info.get('傳真', ''))
            company['公司名稱'] = clean_name
            
    except Exception:
        pass # 失敗保持原樣
        
    return company

# --- 3. 主畫面 ---
col1, col2 = st.columns([3, 1])
with col1:
    search_query = st.text_input("搜尋關鍵字", value="廢水回收系統")
with col2:
    st.write(" ") 
    st.write(" ")
    start_btn = st.button("🚀 開始執行", type="primary", use_container_width=True)

# --- 4. 執行邏輯 ---
if start_btn:
    if not tavily_api_key or not gemini_api_key:
        st.error("❌ 缺少 API Key")
        st.stop()

    # 初始化 API
    tavily = TavilyClient(api_key=tavily_api_key)
    genai.configure(api_key=gemini_api_key)
    
    # 自動偵測模型
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        model.generate_content("test")
    except:
        model = genai.GenerativeModel('gemini-pro')

    status_box = st.status("🚀 任務啟動...", expanded=True)
    progress_bar = st.progress(0)
    result_placeholder = st.empty()
    
    # ==========================
    # 階段一：建立名單 (快速掃描)
    # ==========================
    status_box.write("📡 階段一：廣泛搜尋建立名單...")
    
    suffixes = [" 廠商", " 公司", " 供應商", " 工程"]
    search_keywords = [f"{search_query}{s}" for s in suffixes]
    
    initial_list = []
    seen_urls = set()
    
    for q in search_keywords:
        if len(initial_list) >= target_limit: break
        try:
            res = tavily.search(query=q, max_results=20, search_depth="basic")
            for item in res.get('results', []):
                if item['url'].endswith('.pdf'): continue
                if item['url'] not in seen_urls:
                    initial_list.append({
                        "公司名稱": item['title'],
                        "網址": item['url'],
                        "電話": "", "Email": "", "傳真": ""
                    })
                    seen_urls.add(item['url'])
        except: pass
        progress_bar.progress(min(len(initial_list) / target_limit, 0.2))

    initial_list = initial_list[:target_limit]
    status_box.write(f"✅ 階段一完成，找到 {len(initial_list)} 家公司。啟動多執行緒深度挖掘...")
    
    # ==========================
    # 階段二：極速挖掘 (多執行緒)
    # ==========================
    final_data = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_company = {executor.submit(process_single_company, dict(c), tavily, model): c for c in initial_list}
        
        for idx, future in enumerate(concurrent.futures.as_completed(future_to_company)):
            try:
                data = future.result()
                final_data.append(data)
                
                # 即時顯示
                current_df = pd.DataFrame(final_data)
                cols = ["公司名稱", "電話", "Email", "傳真", "網址"]
                for c in cols: 
                    if c not in current_df.columns: current_df[c] = ""
                
                result_placeholder.dataframe(current_df[cols], use_container_width=True)
                
                # 進度更新
                prog = 0.2 + 0.8 * ((idx + 1) / len(initial_list))
                progress_bar.progress(min(prog, 1.0))
                status_box.write(f"⚡ 已處理: {idx+1}/{len(initial_list)} - {data['公司名稱']}")
                
            except Exception as e:
                pass

    # ==========================
    # 輸出 Excel 美化版 (.xlsx)
    # ==========================
    progress_bar.progress(1.0)
    status_box.update(label=f"🎉 全部完成！共蒐集 {len(final_data)} 筆。", state="complete", expanded=False)
    
    final_df = pd.DataFrame(final_data)
    
    # 欄位補齊與排序
    target_cols = ["公司名稱", "電話", "Email", "傳真", "網址"]
    for c in target_cols:
         if c not in final_df.columns: final_df[c] = ""
    
    # 強制轉字串 (防止電話變科學記號)
    final_df = final_df[target_cols].astype(str)

    # --- 建立 Excel 物件 ---
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        final_df.to_excel(writer, index=False, sheet_name='廠商名單')
        
        # 取得 workbook 和 worksheet 物件
        workbook = writer.book
        worksheet = writer.sheets['廠商名單']
        
        # 定義樣式：標題粗體、置中、淺綠色背景
        header_format = workbook.add_format({
            'bold': True,
            'text_wrap': True,
            'valign': 'top',
            'fg_color': '#D7E4BC', 
            'border': 1
        })
        
        # 設定欄寬 (美化重點)
        worksheet.set_column('A:A', 30) # 公司名稱
        worksheet.set_column('B:B', 20) # 電話
        worksheet.set_column('C:C', 35) # Email
        worksheet.set_column('D:D', 15) # 傳真
        worksheet.set_column('E:E', 50) # 網址
        
        # 套用標題樣式
        for col_num, value in enumerate(final_df.columns.values):
            worksheet.write(0, col_num, value, header_format)
            
    output.seek(0)
    
    st.download_button(
        label=f"📥 下載 Excel 美化報表 ({len(final_df)}筆.xlsx)",
        data=output,
        file_name='公司名單_Pro.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        type="primary"
    )