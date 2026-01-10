import streamlit as st
import pandas as pd
from tavily import TavilyClient
import google.generativeai as genai
import time
import json

# --- 1. 基礎設定 ---
st.set_page_config(page_title="超級名單搜集器 (Secrets版)", layout="wide")
st.title("📊 企業名單自動搜集 (已串接 Secrets)")
st.markdown("專門解決「範圍太廣」的問題：AI 會自動將大關鍵字拆解成數十個精準搜尋詞。")

# --- 2. 讀取 Secrets ---
# 嘗試從 Streamlit Secrets 讀取金鑰
try:
    tavily_api_key = st.secrets["TAVILY_API_KEY"]
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
    st.success("✅ 已成功從 Secrets 載入 API Keys")
except FileNotFoundError:
    st.error("❌ 找不到 secrets.toml 文件，請確認是否已建立 .streamlit/secrets.toml")
    st.stop()
except KeyError as e:
    st.error(f"❌ Secrets 設定檔中缺少變數：{e}，請確認變數名稱是否為 TAVILY_API_KEY 與 GEMINI_API_KEY")
    st.stop()

# --- 3. 側邊欄參數 ---
with st.sidebar:
    st.header("⚙️ 搜尋設定")
    
    # 範圍 100 - 500
    target_limit = st.slider("🎯 目標資料筆數", min_value=100, max_value=500, value=100, step=50)
    st.info(f"設定 {target_limit} 筆時，AI 將會自動規劃約 {int(target_limit/10)+5} 組不同的關鍵字進行地毯式搜索。")

# --- 4. 主畫面 ---
col1, col2 = st.columns([3, 1])
with col1:
    search_query = st.text_input("搜尋關鍵字 (例如：建築業、食品業、廢水處理)", value="廢水回收系統")
with col2:
    st.write(" ") 
    st.write(" ")
    start_btn = st.button("🚀 AI 規劃並執行", type="primary", use_container_width=True)

# --- 5. 執行邏輯 ---
if start_btn:

    # 初始化 API
    tavily = TavilyClient(api_key=tavily_api_key)
    genai.configure(api_key=gemini_api_key)
    model = genai.GenerativeModel('gemini-1.5-pro')

    # ==========================
    # 階段零：AI 關鍵字裂變
    # ==========================
    status_box = st.status("🧠 AI 正在分析產業結構並規劃搜尋策略...", expanded=True)
    
    # 計算需要多少個搜尋詞
    needed_queries = int(target_limit / 10) + 5
    
    plan_prompt = f"""
    使用者想要搜尋關於「{search_query}」的企業名單。
    因為範圍很廣，請你幫我拆解出 {needed_queries} 個「具體且多樣化」的搜尋關鍵字，以便找出該領域上中下游的不同公司。
    
    請包含：
    1. 具體的設備名稱 (例如：RO逆滲透、汙泥壓濾機)
    2. 具體的服務類型 (例如：代操、環保工程、檢測)
    3. 相關的供應鏈角色 (例如：製造商、代理商、經銷商)
    4. 結合台灣主要工業區或地區 (例如：竹科 廢水處理、高雄 環保公司)

    請直接回傳一個 JSON String Array，例如：
    ["{search_query} 設備商", "{search_query} 工程公司", "特定技術 廠商"...]
    
    注意：只回傳 JSON Array，不要有 Markdown。
    """
    
    try:
        plan_res = model.generate_content(plan_prompt)
        plan_text = plan_res.text.replace("```json", "").replace("```", "").strip()
        search_keywords = json.loads(plan_text)
        
        status_box.write(f"✅ 策略規劃完成！AI 生成了 {len(search_keywords)} 組精準搜尋詞：")
        status_box.json(search_keywords)
        
    except Exception as e:
        status_box.warning(f"AI 規劃失敗，切換回預設策略: {e}")
        search_keywords = [f"{search_query} {s}" for s in ["廠商", "公司", "供應商", "工程", "設備", "台北", "台中", "高雄"]]

    # ==========================
    # 階段一：依據 AI 策略進行搜尋
    # ==========================
    status_box.write("📡 開始執行多執行緒搜尋...")
    
    raw_results = []
    seen_urls = set()
    progress_bar = st.progress(0)
    
    # 迴圈抓取
    for i, query in enumerate(search_keywords):
        if len(raw_results) >= target_limit:
            break
            
        status_box.write(f"🔍 ({len(raw_results)}/{target_limit}) 正在搜尋：**{query}**")
        
        try:
            response = tavily.search(
                query=query,
                max_results=20, 
                search_depth="advanced"
            )
            
            items_found = 0
            for item in response.get('results', []):
                url = item.get('url')
                if url and url not in seen_urls:
                    raw_results.append(item) 
                    seen_urls.add(url)
                    items_found += 1
            
            time.sleep(0.5) 
            
        except Exception:
            continue
            
        search_progress = min(len(raw_results) / target_limit, 1.0) * 0.7
        progress_bar.progress(search_progress)

    final_raw_data = raw_results[:target_limit]
    status_box.write(f"✅ 搜尋完成！共取得 {len(final_raw_data)} 筆資料。開始 AI 欄位萃取...")

    # ==========================
    # 階段二：Gemini 整理欄位
    # ==========================
    parsed_data = []
    batch_size = 15 
    
    if len(final_raw_data) > 0:
        total_batches = (len(final_raw_data) + batch_size - 1) // batch_size
        
        for i in range(0, len(final_raw_data), batch_size):
            batch = final_raw_data[i:i+batch_size]
            
            current_batch_idx = i // batch_size
            prog = 0.7 + 0.3 * (current_batch_idx / total_batches)
            progress_bar.progress(min(prog, 0.99))
            
            try:
                batch_json = json.dumps(batch, ensure_ascii=False)
                
                prompt = f"""
                請從 JSON 資料中提取公司聯絡資訊。
                輸出 JSON Array，包含：
                1. "公司名稱"
                2. "Email" (無則空)
                3. "傳真" (無則空)
                4. "電話" (無則空)
                5. "網址" (使用 url)

                原始資料:
                {batch_json}
                """
                
                res = model.generate_content(prompt)
                clean_json = res.text.replace("```json", "").replace("```", "").strip()
                
                try:
                    batch_result = json.loads(clean_json)
                    parsed_data.extend(batch_result)
                except:
                    for item in batch:
                        parsed_data.append({"公司名稱": item.get('title'), "Email":"", "傳真":"", "電話":"", "網址": item.get('url')})
                
            except:
                for item in batch:
                    parsed_data.append({"公司名稱": item.get('title'), "Email":"", "傳真":"", "電話":"", "網址": item.get('url')})
            
            time.sleep(1.0) 

    progress_bar.progress(1.0)
    status_box.update(label="🎉 處理完成！", state="complete", expanded=False)

    # ==========================
    # 階段三：產出 Excel
    # ==========================
    df = pd.DataFrame(parsed_data)
    target_cols = ["公司名稱", "Email", "傳真", "網址", "電話"]
    for col in target_cols:
        if col not in df.columns: df[col] = ""
    df = df[target_cols]

    st.subheader(f"檔案預覽 (共 {len(df)} 筆)")
    st.dataframe(df.head(), use_container_width=True)
    
    csv = df.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label=f"📥 下載 Excel 檔案 ({len(df)}筆資料.csv)",
        data=csv,
        file_name=f'{search_query}_名單.csv',
        mime='text/csv',
        type="primary"
    )