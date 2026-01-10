import streamlit as st
import pandas as pd
from tavily import TavilyClient
import google.generativeai as genai
import time
import json
import re

# ==========================================
# 🔑 設定 API Key (優先從 Secrets 讀取)
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
st.set_page_config(page_title="企業名單搜集器 (萬用版)", layout="wide")
st.title("📊 企業名單自動搜集 (自動偵測模型版)")
st.markdown("已加入「自動偵測」功能，系統會自動尋找您帳號可用的 Gemini 模型，不再報錯。")

if api_source == "Secrets":
    st.success("✅ 已成功從 Secrets 載入 API Keys")
else:
    st.warning("⚠️ 未偵測到 Secrets，請確認代碼中是否已填入 API Key。")

# --- 2. 側邊欄參數 ---
with st.sidebar:
    st.header("⚙️ 設定")
    if not tavily_api_key:
        tavily_api_key = st.text_input("Tavily API Key", type="password")
    if not gemini_api_key:
        gemini_api_key = st.text_input("Gemini API Key", type="password")
        
    st.divider()
    target_limit = st.slider("🎯 目標資料筆數", 100, 500, 100, 50)
    st.info(f"目標：{target_limit} 筆。")

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
        st.error("❌ 缺少 API Key！")
        st.stop()

    # 初始化 Tavily
    tavily = TavilyClient(api_key=tavily_api_key)
    
    # 初始化 Gemini 並自動尋找可用模型
    genai.configure(api_key=gemini_api_key)
    
    status_box = st.status("🔧 正在檢測可用的 AI 模型...", expanded=True)
    
    # === 自動偵測模型邏輯 (關鍵修復) ===
    valid_model_name = "gemini-pro" # 最保險的預設值
    try:
        available_models = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                available_models.append(m.name)
        
        # 優先順序：Flash > 1.5 Pro > 1.0 Pro
        if any('flash' in m for m in available_models):
            valid_model_name = next(m for m in available_models if 'flash' in m)
        elif any('1.5-pro' in m for m in available_models):
            valid_model_name = next(m for m in available_models if '1.5-pro' in m)
        elif 'models/gemini-pro' in available_models:
            valid_model_name = 'models/gemini-pro'
            
        status_box.write(f"✅ 成功連線！將使用模型：**{valid_model_name}**")
        model = genai.GenerativeModel(valid_model_name)
        
    except Exception as e:
        status_box.warning(f"偵測模型列表失敗，嘗試強制使用舊版模型 gemini-pro。錯誤: {e}")
        model = genai.GenerativeModel('gemini-pro')

    # ==========================
    # 階段一：搜尋
    # ==========================
    status_box.write("🚀 啟動搜尋引擎...")
    
    suffixes = [
        " 廠商", " 公司", " 供應商", " 工程", " 設備", 
        " 聯繫方式", " 電話", " 企業名錄", " 推薦", " 解決方案",
        " 台北", " 台中", " 高雄", " 台南", " 新竹", " 桃園",
        " 環保工程", " 水處理", " 廢水代操", " 汙泥處理"
    ]
    search_keywords = [f"{search_query}{s}" for s in suffixes]
    
    if target_limit > 200:
        search_keywords *= 2
    
    raw_results = []
    seen_urls = set()
    progress_bar = st.progress(0)
    
    for i, query in enumerate(search_keywords):
        if len(raw_results) >= target_limit:
            break
        
        status_box.write(f"🔍 ({len(raw_results)}/{target_limit}) 正在搜尋：**{query}**")
        
        try:
            response = tavily.search(
                query=query,
                max_results=20, 
                search_depth="advanced", 
                include_domains=[] 
            )
            
            for item in response.get('results', []):
                url = item.get('url')
                if url and url not in seen_urls:
                    raw_results.append(item)
                    seen_urls.add(url)
            
            time.sleep(0.5)
            
        except Exception:
            continue
            
        progress_bar.progress(min(len(raw_results) / target_limit, 1.0) * 0.7)

    final_raw_data = raw_results[:target_limit]
    status_box.write(f"✅ 搜尋完成！取得 {len(final_raw_data)} 筆資料。開始 AI 智能萃取...")

    # ==========================
    # 階段二：AI 萃取
    # ==========================
    parsed_data = []
    batch_size = 8 # 保守一點，設小一點避免出錯
    
    if len(final_raw_data) > 0:
        total_batches = (len(final_raw_data) + batch_size - 1) // batch_size
        
        for i in range(0, len(final_raw_data), batch_size):
            batch = final_raw_data[i:i+batch_size]
            
            prog = 0.7 + 0.3 * ((i // batch_size) / total_batches)
            progress_bar.progress(min(prog, 0.99))
            
            try:
                # 只傳標題和內容前 800 字，避免 token 爆炸
                mini_batch = [{"title": d['title'], "url": d['url'], "content": d.get('content', '')[:800]} for d in batch]
                batch_json = json.dumps(mini_batch, ensure_ascii=False)
                
                prompt = f"""
                你是資料處理機器人。請從 JSON 中提取公司聯絡資訊。
                
                欄位要求：
                1. "公司名稱" (去除贅字)
                2. "Email" (無則空)
                3. "電話" (無則空)
                4. "傳真" (無則空)
                5. "網址" (使用 url)

                原始資料:
                {batch_json}
                
                請回傳 JSON Array。不要有 Markdown。
                """
                
                res = model.generate_content(prompt)
                clean_json = res.text.replace("```json", "").replace("```", "").strip()
                
                try:
                    batch_result = json.loads(clean_json)
                    parsed_data.extend(batch_result)
                except:
                    # JSON 解析失敗，回填基本資料
                    for item in batch:
                        parsed_data.append({"公司名稱": item.get('title'), "Email":"", "電話":"", "傳真":"", "網址": item.get('url')})

            except Exception as e:
                # AI 呼叫失敗，回填基本資料
                for item in batch:
                    parsed_data.append({"公司名稱": item.get('title'), "Email":"", "電話":"", "傳真":"", "網址": item.get('url')})
            
            time.sleep(1.0)

    progress_bar.progress(1.0)
    status_box.update(label="🎉 處理完成！", state="complete", expanded=False)

    # ==========================
    # 階段三：產出
    # ==========================
    df = pd.DataFrame(parsed_data)
    
    target_cols = ["公司名稱", "電話", "Email", "傳真", "網址"]
    for col in target_cols:
        if col not in df.columns: df[col] = ""
    df = df[target_cols]
    
    st.subheader(f"預覽 (共 {len(df)} 筆)")
    st.dataframe(df.head(), use_container_width=True)
    
    csv = df.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label=f"📥 下載 Excel 檔案 ({len(df)}筆.csv)",
        data=csv,
        file_name=f'{search_query}_名單_自動偵測版.csv',
        mime='text/csv',
        type="primary"
    )