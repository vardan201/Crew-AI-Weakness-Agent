from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ValidationError
import uuid
from datetime import datetime
from typing import Dict
import json
import time
from dotenv import load_dotenv

load_dotenv()

import warnings
import logging
logging.getLogger("litellm").setLevel(logging.ERROR)
warnings.filterwarnings("ignore", message=".*apscheduler.*")

from .models import StartupInput, AgentWeaknessOutput, WeaknessAnalysisResult
from .main import run, prepare_inputs
from .crew import BoardPanelCrew

app = FastAPI(
    title="Board Panel - Weaknesses Analysis API",
    description="AI-powered startup advisory - WEAKNESSES analysis only",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory storage for analysis results
analysis_results: Dict[str, dict] = {}

# Rate limiter: 1200 requests per minute = 20 requests per second
RATE_LIMIT_REQUESTS = 20
RATE_LIMIT_WINDOW = 1.0  # seconds
request_timestamps = []


class AnalysisRequest(BaseModel):
    startup_data: StartupInput


def check_rate_limit():
    """Check if we're within rate limits (1200 req/min = 20 req/sec)"""
    global request_timestamps
    current_time = time.time()
    
    # Remove timestamps older than the window
    request_timestamps = [ts for ts in request_timestamps if current_time - ts < RATE_LIMIT_WINDOW]
    
    if len(request_timestamps) >= RATE_LIMIT_REQUESTS:
        return False
    
    request_timestamps.append(current_time)
    return True


def extract_weaknesses_from_json(output_text: str) -> list:
    """Extract weaknesses list from JSON output with robust parsing."""
    try:
        # Clean the output - remove markdown code blocks
        cleaned = output_text.strip()
        
        # Remove markdown code blocks (```json ... ``` or ``` ... ```)
        if '```' in cleaned:
            # Try to extract content between code blocks
            parts = cleaned.split('```')
            for part in parts:
                part = part.strip()
                # Skip language identifiers like 'json'
                if part and not part.lower() in ['json', 'javascript', 'js']:
                    if '{' in part:
                        cleaned = part
                        break
        
        # Find the JSON object
        if '{' in cleaned:
            start_idx = cleaned.find('{')
            end_idx = cleaned.rfind('}') + 1
            if start_idx != -1 and end_idx > start_idx:
                json_str = cleaned[start_idx:end_idx]
                
                # Try to parse JSON
                try:
                    data = json.loads(json_str)
                except json.JSONDecodeError:
                    # Try fixing common JSON issues
                    json_str = json_str.replace("'", '"')  # Replace single quotes
                    json_str = json_str.replace('\n', ' ')  # Remove newlines that break JSON
                    data = json.loads(json_str)
                
                # Extract weaknesses
                if 'weaknesses' in data and isinstance(data['weaknesses'], list):
                    return data['weaknesses']
                    
                # Sometimes the agent returns nested structure
                if 'result' in data and 'weaknesses' in data['result']:
                    return data['result']['weaknesses']
        
        # Fallback: Try to extract numbered/bulleted list if JSON parsing fails
        print(f"⚠ No valid JSON found. Attempting fallback extraction...")
        weaknesses = []
        lines = output_text.split('\n')
        for line in lines:
            line = line.strip()
            # Look for numbered items (1., 2., etc.) or bullet points (-, *, etc.)
            if line and (line[0].isdigit() or line.startswith(('-', '*', '•'))):
                # Remove numbering/bullets
                clean_line = line.lstrip('0123456789.-*• ').strip()
                if len(clean_line) > 20:  # Only substantial weaknesses
                    weaknesses.append(clean_line)
        
        if weaknesses:
            print(f"✅ Fallback extraction found {len(weaknesses)} weaknesses")
            return weaknesses[:5]  # Max 5 weaknesses
        
        print(f"❌ No weaknesses could be extracted. Preview: {output_text[:200]}...")
        return []
    
    except (json.JSONDecodeError, KeyError) as e:
        print(f"❌ Error parsing JSON: {e}")
        print(f"Output text preview: {output_text[:300]}...")
        return []
    except Exception as e:
        print(f"❌ Unexpected error in extract_weaknesses_from_json: {e}")
        print(f"Output text: {output_text[:300]}...")
        return []


def run_analysis(analysis_id: str, startup_data: StartupInput):
    """Background task with rate limit handling and retry logic."""
    max_retries = 3
    base_delay = 60
    
    try:
        analysis_results[analysis_id]["status"] = "processing"
        
        inputs = prepare_inputs(startup_data)
        
        for attempt in range(max_retries):
            try:
                while not check_rate_limit():
                    time.sleep(0.1)
                
                if attempt > 0:
                    delay = base_delay * (2 ** (attempt - 1))
                    print(f"Rate limit hit. Waiting {delay}s before retry {attempt + 1}/{max_retries}...")
                    analysis_results[analysis_id]["status"] = f"rate_limited_retry_{attempt}"
                    time.sleep(delay)
                
                crew_result = BoardPanelCrew().crew().kickoff(inputs=inputs)
                break
                
            except Exception as e:
                error_msg = str(e)
                if "rate_limit" in error_msg.lower() or "429" in error_msg or "quota" in error_msg.lower():
                    if attempt < max_retries - 1:
                        continue
                raise
        
        # Extract tasks output
        if hasattr(crew_result, 'tasks_output'):
            tasks_output = crew_result.tasks_output
        else:
            tasks_output = []
        
        print(f"\n=== Processing {len(tasks_output)} task outputs ===")
        
        # Initialize result structure
        result = WeaknessAnalysisResult()
        
        # The tasks are executed in this order (from crew.py @crew decorator):
        # Order is determined by self.tasks which uses @task decorated methods
        # in the order they're defined in the class
        task_order = [
            ("marketing", "Marketing Advisor"),
            ("tech", "Tech Lead"),
            ("org_hr", "Org/HR Strategist"),
            ("competitive", "Competitive Analyst"),
            ("finance", "Finance Advisor")
        ]
        
        print(f"Expected task order: {[name for _, name in task_order]}")
        print(f"Number of tasks received: {len(tasks_output)}")
        
        if len(tasks_output) != len(task_order):
            print(f"⚠️ WARNING: Expected {len(task_order)} tasks but got {len(tasks_output)}!")
        
        for idx, task_output in enumerate(tasks_output):
            # Get the raw output text
            if hasattr(task_output, 'raw'):
                output_text = str(task_output.raw)
            else:
                output_text = str(task_output)
            
            print(f"\n--- Task {idx + 1} ---")
            print(f"Output length: {len(output_text)} characters")
            print(f"Output preview: {output_text[:500]}...")
            if len(output_text) > 500:
                print(f"Output ending: ...{output_text[-200:]}")
            
            # Extract weaknesses from JSON
            weaknesses = extract_weaknesses_from_json(output_text)
            print(f"Extracted {len(weaknesses)} weaknesses from this task")
            
            # Map to correct category based on task order
            if idx < len(task_order):
                category, agent_name = task_order[idx]
                
                if category == "marketing":
                    result.marketing_weaknesses = weaknesses
                    print(f"✓ Marketing: {len(weaknesses)} weaknesses")
                elif category == "tech":
                    result.tech_weaknesses = weaknesses
                    print(f"✓ Tech: {len(weaknesses)} weaknesses")
                elif category == "org_hr":
                    result.org_hr_weaknesses = weaknesses
                    print(f"✓ Org/HR: {len(weaknesses)} weaknesses")
                elif category == "competitive":
                    result.competitive_weaknesses = weaknesses
                    print(f"✓ Competitive: {len(weaknesses)} weaknesses")
                elif category == "finance":
                    result.finance_weaknesses = weaknesses
                    print(f"✓ Finance: {len(weaknesses)} weaknesses")
            else:
                print(f"⚠ Unexpected extra task at index {idx}")
        
        print("\n=== Final Result Summary ===")
        print(f"Marketing: {len(result.marketing_weaknesses)} weaknesses")
        print(f"Tech: {len(result.tech_weaknesses)} weaknesses")
        print(f"Org/HR: {len(result.org_hr_weaknesses)} weaknesses")
        print(f"Competitive: {len(result.competitive_weaknesses)} weaknesses")
        print(f"Finance: {len(result.finance_weaknesses)} weaknesses")
        
        # Store validated result
        analysis_results[analysis_id]["status"] = "completed"
        analysis_results[analysis_id]["result"] = result.dict()
        analysis_results[analysis_id]["completed_at"] = datetime.now().isoformat()
        
    except Exception as e:
        print(f"\n!!! Analysis failed with error: {e}")
        import traceback
        traceback.print_exc()
        analysis_results[analysis_id]["status"] = "failed"
        analysis_results[analysis_id]["error"] = str(e)
        analysis_results[analysis_id]["failed_at"] = datetime.now().isoformat()


@app.post("/analyze")
async def analyze(request: AnalysisRequest, background_tasks: BackgroundTasks):
    """
    Submit startup data for weakness analysis.
    Returns an analysis_id to track progress.
    """
    analysis_id = str(uuid.uuid4())
    
    analysis_results[analysis_id] = {
        "status": "queued",
        "submitted_at": datetime.now().isoformat(),
        "result": None,
        "error": None
    }
    
    background_tasks.add_task(run_analysis, analysis_id, request.startup_data)
    
    return {
        "analysis_id": analysis_id,
        "status": "queued",
        "message": "Weaknesses analysis queued successfully"
    }


@app.get("/results/{analysis_id}")
async def get_results(analysis_id: str):
    """
    Get the complete analysis results by analysis_id.
    Returns status, result data, and metadata.
    """
    if analysis_id not in analysis_results:
        raise HTTPException(status_code=404, detail="Analysis ID not found")
    
    analysis = analysis_results[analysis_id]
    
    response = {
        "analysis_id": analysis_id,
        "status": analysis["status"],
        "submitted_at": analysis["submitted_at"]
    }
    
    if analysis["status"] == "completed":
        response["result"] = analysis["result"]
        response["completed_at"] = analysis.get("completed_at")
    elif analysis["status"] == "failed":
        response["error"] = analysis.get("error")
        response["failed_at"] = analysis.get("failed_at")
    
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
