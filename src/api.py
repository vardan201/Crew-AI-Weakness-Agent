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

from src.models import StartupInput, AgentWeaknessOutput, WeaknessAnalysisResult
from src.main import run, prepare_inputs
from src.crew import BoardPanelCrew

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
    """Extract weaknesses list from JSON output."""
    try:
        # Clean the output - remove any markdown code blocks
        cleaned = output_text.strip()
        if cleaned.startswith('```'):
            lines = cleaned.split('\n')
            cleaned = '\n'.join(lines[1:-1]) if len(lines) > 2 else cleaned
        
        # Find the JSON object
        if '{' in cleaned:
            start_idx = cleaned.find('{')
            end_idx = cleaned.rfind('}') + 1
            if start_idx != -1 and end_idx > start_idx:
                json_str = cleaned[start_idx:end_idx]
                data = json.loads(json_str)
                
                # Extract weaknesses
                if 'weaknesses' in data and isinstance(data['weaknesses'], list):
                    return data['weaknesses']
        
        return []
    
    except (json.JSONDecodeError, KeyError) as e:
        print(f"Error parsing JSON: {e}")
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
        
        # The tasks are executed in this order (from crew.py):
        # 1. marketing_analysis_task
        # 2. tech_analysis_task
        # 3. org_hr_analysis_task
        # 4. competitive_analysis_task
        # 5. finance_analysis_task
        
        task_order = [
            ("marketing", "Marketing Advisor"),
            ("tech", "Tech Lead"),
            ("org_hr", "Org/HR Strategist"),
            ("competitive", "Competitive Analyst"),
            ("finance", "Finance Advisor")
        ]
        
        for idx, task_output in enumerate(tasks_output):
            # Get the raw output text
            if hasattr(task_output, 'raw'):
                output_text = str(task_output.raw)
            else:
                output_text = str(task_output)
            
            print(f"\n--- Task {idx + 1} ---")
            print(f"Output preview: {output_text[:200]}...")
            
            # Extract weaknesses from JSON
            weaknesses = extract_weaknesses_from_json(output_text)
            
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
