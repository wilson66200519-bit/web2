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
    # 嘗試從 Streamlit Secrets 讀取
    tavily_api_key = st.secrets["TAVILY_API_KEY"]
    gemini_api_key = st.secrets["GEMINI_API_KEY"]
    api_source = "Secrets"
except:
    # 如果沒設定 Secrets，預設為空 (需手動填寫或在代碼中填寫)
    tavily_api_key = ""
    gemini_api_key = ""
    api_source = "None"

# --- 1. 基礎設定 ---
st.set_page_config(page_title="企業名單搜集器 (修復版)", layout="wide")
st.title("📊 企業名單自動搜集 (修復版)")
st.markdown("已修正 AI 模型連線問題，並加強電話與 Email 的提取能力。")

if api_source == "Secrets":
    st.success("✅ 已成功從 Secrets 載入 API Keys")
else:
    st.warning("⚠️ 未偵測到 Secrets，請確認代碼中是否已填入 API Key，或於左側輸入。")

# --- 2. 側邊欄參數 ---
with st.sidebar:
    st.header("⚙️ 設定")
    
    # 如果沒有 Secrets，開放手動輸入
    if not tavily_api_key:
        tavily_api_key = st.text_input("Tavily API Key", type="password")
    if not gemini_api_key:
        gemini_api_key = st.text_input("Gemini API Key", type="password")
        
    st.divider()
    target_limit = st.slider("🎯 目標資料筆數", 100, 500, 100, 50)
    st.info(f"目標：{target_limit} 筆。系統將自動執行多輪搜尋。")

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

    # 初始化
    tavily = TavilyClient(api_key=tavily_api_key)
    genai.configure(api_key=gemini_api_key)
    
    # ✅ 關鍵修正：使用 'gemini-1.5-flash' 避免 404 錯誤
    try:
        model = genai.GenerativeModel('gemini-1.5-flash')
        # 測試連線
        model.generate_content("test")
    except Exception as e:
        st.error(f"❌ 模型連線失敗：{e}")
        st.stop()

    # ==========================
    # 階段一：搜尋
    # ==========================
    status_box = st.status("🚀 啟動搜尋引擎...", expanded=True)
    
    # 產生多樣化關鍵字
    suffixes = [
        " 廠商", " 公司", " 供應商", " 工程", " 設備", 
        " 聯繫方式", " 電話", " 企業名錄", " 推薦", " 解決方案",
        " 台北", " 台中", " 高雄", " 台南", " 新竹", " 桃園",
        " 環保工程", " 水處理", " 廢水代操", " 汙泥處理"
    ]
    # 組合關鍵字
    search_keywords = [f"{search_query}{s}" for s in suffixes]
    # 如果要抓 500 筆，就重複利用或增加更多
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
                search_depth="advanced", # 必須使用 advanced 才能抓到內文
                include_domains=[] 
            )
            
            for item in response.get('results', []):
                url = item.get('url')
                if url and url not in seen_urls:
                    raw_results.append(item)
                    seen_urls.add(url)
            
            time.sleep(0.5)
            
        except Exception as e:
            print(f"Search error: {e}")
            continue
            
        progress_bar.progress(min(len(raw_results) / target_limit, 1.0) * 0.7)

    final_raw_data = raw_results[:target_limit]
    status_box.write(f"✅ 搜尋完成！取得 {len(final_raw_data)} 筆資料。開始 AI 智能萃取...")

    # ==========================
    # 階段二：AI 萃取 (加強版)
    # ==========================
    parsed_data = []
    batch_size = 10 # 縮小批次大小，提高 AI 成功率
    
    if len(final_raw_data) > 0:
        total_batches = (len(final_raw_data) + batch_size - 1) // batch_size
        
        for i in range(0, len(final_raw_data), batch_size):
            batch = final_raw_data[i:i+batch_size]
            
            prog = 0.7 + 0.3 * ((i // batch_size) / total_batches)
            progress_bar.progress(min(prog, 0.99))
            
            try:
                # 簡化 JSON，只留給 AI 需要的欄位，減少 token 消耗與混淆
                mini_batch = [{"title": d['title'], "url": d['url'], "content": d.get('content', '')[:1000]} for d in batch]
                batch_json = json.dumps(mini_batch, ensure_ascii=False)
                
                # ✅ 強化提示詞 (Prompt Engineering)
                prompt = f"""
                你是資料處理專家。請從下方 JSON 資料中，精準提取每家公司的聯絡資訊。
                
                目標欄位：
                1. "公司名稱" (請從標題或內文分析出最乾淨的公司全名，去除 '首頁'、'有限公司' 後面的贅字)
                2. "Email" (尋找 @ 符號的信箱，若無則留空)
                3. "電話" (尋找手機或市話格式，若無則留空)
                4. "傳真" (若無則留空)
                5. "網址" (直接回填 url)

                原始資料:
                {batch_json}
                
                請直接回傳 JSON Array，格式範例：
                [{{"公司名稱": "某某科技", "Email": "abc@test.com", "電話": "02-12345678", "傳真": "", "網址": "..."}}]
                嚴禁輸出 Markdown 標記 (不要有 ```json)。
                """
                
                res = model.generate_content(prompt)
                clean_json = res.text.replace("```json", "").replace("```", "").strip()
                
                batch_result = json.loads(clean_json)
                parsed_data.extend(batch_result)
                
            except Exception as e:
                # 如果這批失敗，至少保留標題網址，不要全空
                for item in batch:
                    parsed_data.append({
                        "公司名稱": item.get('title'),
                        "Email": "", "電話": "", "傳真": "", "網址": item.get('url')
                    })
            
            time.sleep(1.0)

    progress_bar.progress(1.0)
    status_box.update(label="🎉 處理完成！", state="complete", expanded=False)

    # ==========================
    # 階段三：產出
    # ==========================
    df = pd.DataFrame(parsed_data)
    
    # 確保欄位存在且順序正確
    target_cols = ["公司名稱", "電話", "Email", "傳真", "網址"]
    for col in target_cols:
        if col not in df.columns: df[col] = ""
    df = df[target_cols]

    # 去除完全空白的無效資料 (標題不算)
    # 這裡可以加強：如果電話/Email都沒抓到，是否要標註？目前先保留原樣。
    
    st.subheader(f"預覽 (共 {len(df)} 筆)")
    st.dataframe(df.head(), use_container_width=True)
    
    csv = df.to_csv(index=False).encode('utf-8-sig')
    st.download_button(
        label=f"📥 下載 Excel 檔案 ({len(df)}筆.csv)",
        data=csv,
        file_name=f'{search_query}_名單_修復版.csv',
        mime='text/csv',
        type="primary"
    )