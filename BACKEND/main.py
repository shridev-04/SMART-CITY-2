import uvicorn
import os

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    is_prod = bool(os.environ.get("RENDER"))
    print(f"Starting Smart City AI Platform on port {port}")
    uvicorn.run("api.index:app", host="0.0.0.0", port=port, reload=not is_prod)
