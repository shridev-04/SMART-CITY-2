import uvicorn
import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"Starting Smart City AI Platform on http://localhost:{port}")
    print("Dashboard available at: http://localhost:8000/dashboard")
    uvicorn.run("api.index:app", host="0.0.0.0", port=port, reload=True)
