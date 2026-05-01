#!/usr/bin/env python3
"""
frame_selector.py — Pass 2: Select best frames for each marker using vision.

Given a Recording (gallery of timestamped frames) and the guide markers,
uses Claude's vision to pick the best matching frame for each marker.
"""

import base64
import json
import os
import sys
from pathlib import Path
from typing import Optional

from .guide import Marker


def select_frames(
    frames: list[dict],  # List of frame dicts with index, url, title, action, png_path
    markers: list[Marker],
    verbose: bool = True,
) -> dict[int, str]:
    """
    For each marker, ask Claude vision to pick the best frame.

    Args:
        frames: Gallery of captured frames (with png_path)
        markers: Screenshot markers from the guide
        verbose: Print progress

    Returns:
        Dict mapping marker_index → base64 data URI of the selected frame
    """
    try:
        from litellm import completion
    except ImportError:
        print("ERROR: litellm required for frame selection. pip install litellm", file=sys.stderr)
        sys.exit(1)

    model_id = os.environ.get("LLM_MODEL", "bedrock/us.anthropic.claude-sonnet-4-6")
    results: dict[int, str] = {}

    def _log(msg: str):
        if verbose:
            print(f"  [selector] {msg}", file=sys.stderr)

    # Load all frame images (limit to 20 per Claude's image limit)
    frame_images = []
    for f in frames:
        png_path = f.get("png_path")
        if not png_path or not Path(png_path).exists():
            continue
        png_bytes = Path(png_path).read_bytes()
        b64 = base64.b64encode(png_bytes).decode("ascii")
        frame_images.append({
            "index": f["index"],
            "url": f.get("url", ""),
            "title": f.get("title", ""),
            "action": f.get("action", ""),
            "base64": b64,
        })

    _log(f"Loaded {len(frame_images)} frames for vision analysis")

    if not frame_images:
        _log("No frames to analyze")
        return results

    # Process each marker
    for marker in markers:
        _log(f"Selecting frame for marker [{marker.index}]: {marker.description}")

        # Build the message with all frame images
        # If more than 18 frames, take evenly spaced samples
        if len(frame_images) > 18:
            step = len(frame_images) / 18
            sampled = [frame_images[int(i * step)] for i in range(18)]
        else:
            sampled = frame_images

        # Build content blocks: text description + all images with labels
        content = [
            {
                "type": "text",
                "text": f"""I have a gallery of {len(sampled)} screenshots captured during an Okta Admin Console session. I need you to select the ONE frame that best matches this description:

**"{marker.description}"**

Below are the frames. Each is labeled with its index number, URL, and the action that preceded it. Pick the frame that shows the page/feature described above.

Respond with ONLY a JSON object: {{"selected_frame": <index_number>, "reason": "<brief reason>"}}

Frame metadata:
""" + "\n".join(f"  Frame {f['index']}: URL={f['url'][:80]}, Title={f['title'][:40]}, Action={f['action'][:40]}" for f in sampled)
            }
        ]

        # Add each frame as an image
        for f in sampled:
            content.append({
                "type": "text",
                "text": f"\n--- Frame {f['index']} ---"
            })
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{f['base64']}"
                }
            })

        messages = [{"role": "user", "content": content}]

        litellm_kwargs = {
            "model": model_id,
            "messages": messages,
            "max_tokens": 256,
        }
        if os.environ.get("LITELLM_API_BASE"):
            litellm_kwargs["api_base"] = os.environ["LITELLM_API_BASE"]
            litellm_kwargs["api_key"] = os.environ.get("LITELLM_API_KEY", "")

        try:
            response = completion(**litellm_kwargs)
            reply = response.choices[0].message.content

            # Parse the JSON response
            # Handle cases where Claude wraps in markdown code blocks
            if "```" in reply:
                reply = reply.split("```")[1]
                if reply.startswith("json"):
                    reply = reply[4:]

            result = json.loads(reply.strip())
            selected_idx = result.get("selected_frame")
            reason = result.get("reason", "")

            _log(f"  Selected frame {selected_idx}: {reason}")

            # Find the frame and get its base64
            selected = next((f for f in frame_images if f["index"] == selected_idx), None)
            if selected:
                results[marker.index] = f"data:image/png;base64,{selected['base64']}"
            else:
                _log(f"  WARNING: Frame {selected_idx} not found in gallery")

        except Exception as e:
            _log(f"  ERROR selecting frame: {e}")

    _log(f"Selected {len(results)}/{len(markers)} frames")
    return results
