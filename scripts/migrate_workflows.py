import os
import json
import uuid

# Path configuration
WORKFLOW_DIR = "n8n-workflows"
OUTPUT_DIR = "n8n-workflows"

# Target credentials
POSTGRES_CRED = {
    "id": "j422VKBADCTjmDu7",
    "name": "TradeFlat Account Postgres"
}

GEMINI_CRED = {
    "id": "AgJMpXMgoNyxP4iQ",
    "name": "Gemini (TradeFlat)"
}

# Mapping of original node names to their new AI Agent parameters
LLM_MAPPING = {
    "LLM: Extract Metadata": {
        "text": "={{ 'Từ đoạn văn dưới đây, trích xuất cấu trúc. KHÔNG tóm tắt. CHỈ liệt kê.\\n\\nTrả về JSON:\\n{\\n  \"headings\": [\"Tiêu đề chính\"],\\n  \"topics\": [\"Chủ đề A\", \"Chủ đề B\"],\\n  \"key_entities\": [\"Tên riêng\", \"Năm\", \"Sự kiện\"],\\n  \"content_type\": \"narrative|data|argument|mixed\",\\n  \"position_hint\": \"intro|body|conclusion\"\\n}\\n\\nĐoạn văn (chunk ' + $json.chunk_index + '/' + $json.total_chunks + '):\\n' + $json.chunk_text }}"
    },
    "LLM: Create Outline + Mapping": {
        "text": "={{ 'Từ danh sách metadata các chunks dưới đây, hãy:\\n1. Tạo outline cho slide presentation\\n2. Map mỗi section tới chunk_ids chứa nội dung liên quan\\n\\nTrả về JSON:\\n{\\n  \"outline\": [{\"section_id\": 0, \"title\": \"...\", \"slides\": 2, \"key_points\": [\"...\"]}],\\n  \"chunk_mapping\": {\"0\": [0], \"1\": [1,2], \"2\": [3,4]},\\n  \"total_slides\": 15,\\n  \"topic\": \"...\",\\n  \"language\": \"vi\",\\n  \"context\": \"general|vietnam_history|tech|business\",\\n  \"theme_suggestion\": \"midnight\"\\n}\\n\\nUser prompt: ' + $json.prompt + '\\n\\nMerged metadata:\\n' + JSON.stringify($json.merged_metadata) }}"
    },
    "LLM: Direct Outline": {
        "text": "={{ 'Từ prompt sau, hãy tạo outline cho presentation.\\n\\nTrả về JSON:\\n{\\n  \"outline\": [{\"section_id\": 0, \"title\": \"...\", \"slides\": 2, \"key_points\": [\"...\"]}],\\n  \"chunk_mapping\": {},\\n  \"total_slides\": 10,\\n  \"topic\": \"...\",\\n  \"language\": \"vi\",\\n  \"context\": \"general\",\\n  \"theme_suggestion\": \"midnight\"\\n}\\n\\nUser prompt: ' + $json.prompt }}"
    },
    "LLM: Write Slides": {
        "text": "={{ 'Viết ' + $json.num_slides + ' slides cho section: \"' + $json.section_title + '\"\\n\\nKey points cần cover:\\n' + JSON.stringify($json.key_points) + '\\n\\nNỘI DUNG GỐC (viết slides DỰA TRÊN đoạn văn này, KHÔNG tự bịa):\\n' + $json.section_text + '\\n\\nQuy tắc:\\n- Mỗi slide: title + 5-8 bullet points\\n- Mỗi bullet là câu đầy đủ 15-30 từ\\n- GIỮ NGUYÊN ngôn ngữ gốc (VN→VN, EN→EN)\\n- image_keyword bằng tiếng Anh, 2-4 từ cụ thể\\n- Mỗi slide PHẢI có image_keyword KHÁC NHAU\\n\\nTrả về JSON array:\\n[{\"slide_number\": 1, \"title\": \"...\", \"content\": \"- bullet 1\\\\n- bullet 2\\\\n...\", \"narration\": \"\", \"image_keyword\": \"Vietnam ...\"}]' }}"
    },
    "LLM: Select Layouts": {
        "text": "={{ 'Cho mỗi slide dưới đây, chọn layout phù hợp nhất.\\n\\nDanh sách layouts: ' + $json.layout_list + '\\n\\nSlides:\\n' + $json.slides_summary + '\\n\\nTrả về JSON array: [{\"slide_index\": 0, \"layout\": \"cover\"}, {\"slide_index\": 1, \"layout\": \"bullets\"}, ...]' }}"
    },
    "LLM: Select Theme": {
        "text": "={{ 'Cho presentation về chủ đề: ' + ($json.context?.topic || 'general') + '\\nChọn 1 theme phù hợp nhất từ danh sách: midnight, ocean, forest, sunset, crimson, emerald_gold, rose, dark_purple, corporate_blue, cyber_punk, pastel_dream, aurora, volcano, arctic, desert_sand, lavender, neon_city, bamboo_zen, royal_gold, slate_minimal\\n\\nTrả về JSON: {\"theme\": \"theme_name\", \"reason\": \"lý do\"}' }}"
    }
}

# Downstream parser node updates to use response.output
PARSER_UPDATES = {
    "Parse Extract Result": """// Parse Gemini response → metadata
const response = $input.first().json;
const text = response.output || '{}';

try {
  const metadata = JSON.parse(text);
  return [{ json: { chunk_index: $('Split Into Items').item.json.chunk_index, metadata } }];
} catch (e) {
  // Fallback for markdown wrapped json
  const match = text.match(/{[\\s\\S]*}/);
  if (match) {
    try {
      return [{ json: { chunk_index: $('Split Into Items').item.json.chunk_index, metadata: JSON.parse(match[0]) } }];
    } catch(e2) {}
  }
  return [{ json: { chunk_index: 0, metadata: { headings: [], topics: [], key_entities: [], error: e.message } } }];
}""",
    "Parse Outline": """// Parse Gemini outline response (from either branch)
const response = $input.first().json;
const text = response.output || '{}';

try {
  const result = JSON.parse(text.replace(/```json\\n?|```/g, ""));
  return [{ json: { analyst_result: result } }];
} catch (e) {
  const match = text.match(/{[\\s\\S]*}/);
  if (match) {
    try {
      return [{ json: { analyst_result: JSON.parse(match[0]) } }];
    } catch(e2) {}
  }
  throw new Error('Failed to parse outline JSON: ' + e.message);
}""",
    "Parse Slides JSON": """// Parse LLM response → slides array
const response = $input.first().json;
const text = response.output || '[]';

try {
  const slides = JSON.parse(text.replace(/```json\\n?|```/g, ""));
  return [{ json: { section_slides: Array.isArray(slides) ? slides : [slides] } }];
} catch (e) {
  // Try to extract JSON from text
  const match = text.match(/\\[[\\s\\S]*\\]/);
  if (match) {
    try {
      return [{ json: { section_slides: JSON.parse(match[0]) } }];
    } catch (e2) {}
  }
  return [{ json: { section_slides: [], parse_error: e.message } }];
}""",
    "Parse Layouts": """// Parse layout selection
const response = $input.first().json;
const text = response.output || '[]';
let layouts = [];
try { 
  layouts = JSON.parse(text.replace(/```json\\n?|```/g, "")); 
} catch(e) { 
  const match = text.match(/\\[[\\s\\S]*\\]/);
  if (match) {
    try { layouts = JSON.parse(match[0]); } catch(e2) {}
  }
}
return [{ json: { ...($('Build Layout Prompt').first().json), layouts } }];"""
}

def migrate_workflow(filename):
    filepath = os.path.join(WORKFLOW_DIR, filename)
    with open(filepath, 'r', encoding='utf-8') as f:
        workflow = json.load(f)

    nodes = workflow.get("nodes", [])
    connections = workflow.get("connections", {})
    new_nodes = []
    
    modified = False

    for node in nodes:
        node_name = node.get("name")
        node_type = node.get("type")
        
        # 1. Update Postgres credentials
        if node_type == "n8n-nodes-base.postgres":
            node["credentials"] = {
                "postgres": POSTGRES_CRED
            }
            new_nodes.append(node)
            modified = True
            print(f"Updated Postgres credentials for: {node_name}")
            continue

        # 2. Update Code parser nodes
        if node_name in PARSER_UPDATES:
            node["parameters"]["jsCode"] = PARSER_UPDATES[node_name]
            new_nodes.append(node)
            modified = True
            print(f"Updated JS parsing code for: {node_name}")
            continue

        # 3. Replace LLM HTTP Request nodes with LangChain Agent + Chat Model
        if node_name in LLM_MAPPING:
            pos = node.get("position", [0, 0])
            node_id = node.get("id")
            
            # Create the AI Agent node using same ID/Name so connections are preserved
            agent_node = {
                "parameters": {
                    "promptType": "define",
                    "text": LLM_MAPPING[node_name]["text"],
                    "options": {}
                },
                "type": "@n8n/n8n-nodes-langchain.agent",
                "typeVersion": 2.2,
                "position": pos,
                "id": node_id,
                "name": node_name
            }
            
            # Create Gemini Chat Model node
            model_name = f"Model for {node_name}"
            model_node = {
                "parameters": {
                    "options": {}
                },
                "type": "@n8n/n8n-nodes-langchain.lmChatGoogleGemini",
                "typeVersion": 1,
                "position": [pos[0] - 180, pos[1] + 180],
                "id": str(uuid.uuid4()),
                "name": model_name,
                "credentials": {
                    "googlePalmApi": GEMINI_CRED
                }
            }
            
            new_nodes.append(agent_node)
            new_nodes.append(model_node)
            
            # Connect the Model node to the Agent node
            connections[model_name] = {
                "ai_languageModel": [
                    [
                        {
                            "node": node_name,
                            "type": "ai_languageModel",
                            "index": 0
                        }
                    ]
                ]
            }
            
            modified = True
            print(f"Migrated LLM node '{node_name}' to AI Agent + Gemini Model")
            continue
            
        new_nodes.append(node)

    if modified:
        workflow["nodes"] = new_nodes
        workflow["connections"] = connections
        out_path = os.path.join(OUTPUT_DIR, filename)
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(workflow, f, indent=2, ensure_ascii=False)
        print(f"Saved migrated workflow: {out_path}")
    else:
        print(f"No changes made to {filename}")

if __name__ == "__main__":
    files = [
        "01_master_pipeline.json",
        "02_analysis_pipeline.json",
        "03_writing_pipeline.json",
        "04_design_pipeline.json",
        "05_export_pipeline.json"
    ]
    for f in files:
        migrate_workflow(f)
