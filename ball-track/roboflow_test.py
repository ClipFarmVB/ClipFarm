import base64
import os
from PIL import Image
import io
from inference_sdk import InferenceHTTPClient
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

API_KEY       = os.getenv("ROBOFLOW_API_KEY")
WORKSPACE     = os.getenv("ROBOFLOW_WORKSPACE")
WORKFLOW_ID   = os.getenv("ROBOFLOW_WORKFLOW_ID")

# 1. Connect to your workflow
client = InferenceHTTPClient(
    api_url="https://serverless.roboflow.com",
    api_key=API_KEY
)

# 2. Run your workflow on an image
result = client.run_workflow(
    workspace_name=WORKSPACE    ,
    workflow_id=WORKFLOW_ID,
    images={
        "image": "0037.jpg"
    },
    use_cache=True
)

# 3. Decode and save the annotated image
output_image_b64 = result[0]["output_image"]
image_bytes = base64.b64decode(output_image_b64)
image = Image.open(io.BytesIO(image_bytes))
image.save("output.jpg")
image.show()  # Opens the image in your default viewer

# 4. Print just the predictions (cleaner than printing the whole result)
predictions = result[0]["predictions"]["predictions"]
for p in predictions:
    print(f"Ball detected at ({p['x']:.1f}, {p['y']:.1f}) | confidence: {p['confidence']:.2f} | size: {p['width']}x{p['height']}px")