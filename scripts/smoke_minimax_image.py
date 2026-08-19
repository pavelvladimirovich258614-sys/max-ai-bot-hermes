import asyncio
import hashlib
from pathlib import Path

from app.config import get_settings
from app.llm.image_client import ImageClient


async def main() -> None:
    settings = get_settings()
    prompt = (
        "A calm modern legal office workspace, warm natural daylight, "
        "notebook and fountain pen on an oak desk, green plant, no people, "
        "no logos, no text, realistic editorial photography"
    )
    async with ImageClient(settings) as client:
        data = await client.generate(prompt, aspect_ratio="1:1")
    path = Path("logs/minimax_image_smoke.jpg")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    print(f"image_smoke bytes={len(data)} sha256={hashlib.sha256(data).hexdigest()} path={path}")


if __name__ == "__main__":
    asyncio.run(main())
