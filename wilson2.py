import streamlit as st
import pandas as pd
from tavily import TavilyClient
import google.generativeai as genai
import time
import json
import re
import concurrent.futures
import random
import io
import xlsxwriter
from urllib.parse import urlparse # 新增：解析網域用

# ==========================================
# 🔑 設定 API Key
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
st.set_page_config(page_title="企業名單搜集器 (最終修正版)", layout="wide")
st.title("✅ 企業名單搜集 (修正搜尋邏輯版)")
st.markdown("""
**本次修正重點：**
1. 🔧 **搜尋邏輯修復**：不再誤刪公司名稱，改用「完整標題 + 網域」精準搜尋。
2. 📝 **狀態備註**：新增欄位顯示搜尋結果（如：成功、未找到、錯誤），方便除錯。
3. 🛡️ **Excel 內容保證**：即使 AI 沒抓到，也會保留原始標題與網址，不會全空。
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
    max_workers = st.slider("⚡ 同時搜尋線程數", 1, 10, 5)

# --- Helper: 資料清洗 ---
def clean_phone(phone_str):
    """保留數字、分機、括號"""
    if not phone_str: return ""
    # 稍微放寬標準，允許 'ext' 或 '分機'
    cleaned = re.sub(r'[^\d\+\-\(\)\#\s分機ext]', '', str(phone_str))
    return cleaned.strip()

# --- Helper: AI 呼叫 ---
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
    """背景任務：針對單一公司進行深度挖掘"""
    
    # 1. 解析網域 (作為搜尋的強力特徵)
    try:
        domain = urlparse(company['網址']).netloc
    except:
        domain = ""
        
    # 修正：不要隨意切割名字，使用完整標題
    full_name = company['公司名稱']
    
    # 隨機延遲
    time.sleep(random.uniform(0.5, 1.5))
    
    try:
        # 2. 建構精準搜尋詞
        # 策略：直接搜尋該網域內的聯絡頁面，或者搜尋公司全名
        if domain:
            query = f"site:{domain} 聯絡我們 contact 電話 email"
        else:
            query = f"{full_name} 聯絡電話 email"
            
        # 執行搜尋
        search_res = tavily_client.search(query=query, max_results=3, search_depth="advanced")
        context = "\n".join([r['content'] for r in search_res.get('results', [])])
        
        if not context:
            company['狀態'] = "搜尋無結果"
            return company

        # 3. AI 萃取
        prompt = f"""
        任務：從以下搜尋結果中，找出 "{full_name}" 的聯絡資料。
        
        搜尋內容：
        {context[:2000]}
        
        請回傳 JSON 格式：
        {{
            "公司簡稱": "請從標題中分析出最簡短的公司名 (例如 '建越科技')",
            "電話": "找不到留空",
            "Email": "找不到留空",
            "傳真": "找不到留空"
        }}
        只回傳 JSON，不要 Markdown。
        """
        
        ai_text = robust_gemini_call(model, prompt)
        
        if ai_text:
            clean_json = ai_text.replace("```json", "").replace("```", "").strip()
            info = json.loads(clean_json)
            
            # 寫入資料
            company['公司名稱'] = info.get('公司簡稱', full_name) # 更新為更乾淨的名字
            company['電話'] = clean_phone(info.get('電話', ''))
            company['Email'] = info.get('Email', '')
            company['傳真'] = clean_phone(info.get('傳真', ''))
            
            # 判斷是否成功抓到資料
            if company['電話'] or company['Email']:
                company['狀態'] = "✅ 成功"
            else:
                company['狀態'] = "⚠️ 僅有基本資料"
        else:
            company['狀態'] = "AI 解析失敗"
            
    except Exception as e:
        company['狀態'] = f"❌ 錯誤: {str(e)}"
        
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

    tavily = TavilyClient(api_key=tavily_api_key)
    genai.configure(api_key=gemini_api_key)
    
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        model.generate_content("test")
    except:
        model = genai.GenerativeModel('gemini-pro')

    status_box = st.status("🚀 任務啟動...", expanded=True)
    progress_bar = st.progress(0)
    result_placeholder = st.empty()
    
    # ==========================
    # 階段一：建立名單
    # ==========================
    status_box.write("📡 階段一：廣泛搜尋中...")
    
    suffixes = [" 廠商", " 公司", " 供應商", " 工程"]
    search_keywords = [f"{search_query}{s}" for s in suffixes]
    
    initial_list = []
    seen_urls = set()
    
    for q in search_keywords:
        if len(initial_list) >= target_limit: break
        try:
            res = tavily.search(query=q, max_results=20, search_depth="basic")
            for item in res.get('results', []):
                # 過濾非目標網站
                if item['url'].endswith('.pdf'): continue
                
                if item['url'] not in seen_urls:
                    initial_list.append({
                        "公司名稱": item['title'], # 保留完整標題
                        "網址": item['url'],
                        "電話": "", "Email": "", "傳真": "", "狀態": "待處理"
                    })
                    seen_urls.add(item['url'])
        except: pass
        progress_bar.progress(min(len(initial_list) / target_limit, 0.2))

    initial_list = initial_list[:target_limit]
    status_box.write(f"✅ 階段一完成，找到 {len(initial_list)} 家公司。啟動深度挖掘...")
    
    # ==========================
    # 階段二：深度挖掘
    # ==========================
    final_data = []
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_company = {executor.submit(process_single_company, dict(c), tavily, model): c for c in initial_list}
        
        for idx, future in enumerate(concurrent.futures.as_completed(future_to_company)):
            data = future.result()
            final_data.append(data)
            
            # 即時顯示
            current_df = pd.DataFrame(final_data)
            # 確保欄位順序
            cols = ["公司名稱", "電話", "Email", "傳真", "網址", "狀態"]
            for c in cols: 
                if c not in current_df.columns: current_df[c] = ""
            
            result_placeholder.dataframe(current_df[cols], use_container_width=True)
            
            prog = 0.2 + 0.8 * ((idx + 1) / len(initial_list))
            progress_bar.progress(min(prog, 1.0))
            status_box.write(f"⚡ 已處理: {idx+1}/{len(initial_list)} - {data['公司名稱']}")

    # ==========================
    # 輸出 Excel
    # ==========================
    progress_bar.progress(1.0)
    status_box.update(label=f"🎉 完成！共 {len(final_data)} 筆。", state="complete", expanded=False)
    
    final_df = pd.DataFrame(final_data)
    target_cols = ["公司名稱", "電話", "Email", "傳真", "網址", "狀態"]
    for c in target_cols:
         if c not in final_df.columns: final_df[c] = ""
    
    final_df = final_df[target_cols].astype(str)

    # 寫入 Excel
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        final_df.to_excel(writer, index=False, sheet_name='廠商名單')
        workbook = writer.book
        worksheet = writer.sheets['廠商名單']
        
        header_format = workbook.add_format({
            'bold': True, 'text_wrap': True, 'valign': 'top',
            'fg_color': '#D7E4BC', 'border': 1
        })
        
        worksheet.set_column('A:A', 30) # 公司名稱
        worksheet.set_column('B:B', 20) # 電話
        worksheet.set_column('C:C', 30) # Email
        worksheet.set_column('D:D', 15) # 傳真
        worksheet.set_column('E:E', 40) # 網址
        worksheet.set_column('F:F', 15) # 狀態
        
        for col_num, value in enumerate(final_df.columns.values):
            worksheet.write(0, col_num, value, header_format)
            
    output.seek(0)
    
    st.download_button(
        label=f"📥 下載 Excel 檔案 ({len(final_df)}筆.xlsx)",
        data=output,
        file_name='公司名單_Final_Fixed.xlsx',
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        type="primary"
    )