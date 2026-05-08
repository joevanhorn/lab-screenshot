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

    model_id = os.environ.get("LLM_MODEL", "claude-sonnet-4-6")
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
                "text": f"""I have a gallery of {len(sampled)} screenshots captured during a browser automation session. I need you to select the ONE frame that best matches this description:

**"{marker.description}"**

Below are the frames with their index numbers, URLs, and preceding actions.

IMPORTANT RULES:
- You MUST select a frame. Do not refuse or say none match.
- Pick the CLOSEST match, even if it's not perfect. A partial match is better than no match.
- If multiple frames could work, pick the one that best represents what the description asks for.

Respond with a JSON object on a single line: {{"selected_frame": <index_number>, "reason": "<brief reason>"}}

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
            "max_tokens": 512,
        }
        if os.environ.get("LITELLM_API_BASE"):
            litellm_kwargs["api_base"] = os.environ["LITELLM_API_BASE"]
            litellm_kwargs["api_key"] = os.environ.get("LITELLM_API_KEY", "")

        for attempt in range(2):  # Retry once on failure
            try:
                response = completion(**litellm_kwargs)
                reply = response.choices[0].message.content

                if not reply or not reply.strip():
                    _log(f"  Empty response from LLM (attempt {attempt + 1})")
                    continue

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
                break  # Success, no retry needed

            except json.JSONDecodeError as e:
                _log(f"  JSON parse error (attempt {attempt + 1}): {e}")
                _log(f"  Raw reply: {repr(reply[:200]) if reply else '(empty)'}")
                if attempt == 0:
                    _log(f"  Retrying with fewer frames...")
                    # Reduce frame count on retry
                    if len(sampled) > 10:
                        sampled = sampled[::2]  # Take every other frame
                    # Also rebuild the content with fewer frames
                    content = [content[0]]  # Keep the text prompt
                    for f in sampled:
                        content.append({"type": "text", "text": f"\n--- Frame {f['index']} ---"})
                        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{f['base64']}"}})
                    litellm_kwargs["messages"] = [{"role": "user", "content": content}]
            except Exception as e:
                _log(f"  ERROR selecting frame: {e}")
                break

        # Fallback: if LLM failed, pick the best frame heuristically
        if marker.index not in results and frame_images:
            desc_lower = marker.description.lower()
            best_frame = None
            best_score = -1

            for f in frame_images:
                score = 0
                action_lower = f.get("action", "").lower()
                title_lower = f.get("title", "").lower()
                url_lower = f.get("url", "").lower()

                # Score based on keyword matches between description and frame metadata
                for keyword in desc_lower.split():
                    if len(keyword) > 3:  # Skip short words
                        if keyword in action_lower:
                            score += 2
                        if keyword in title_lower:
                            score += 2
                        if keyword in url_lower:
                            score += 1

                # Prefer frames from the middle/end of the session (more likely to show results)
                score += f["index"] / len(frame_images)

                if score > best_score:
                    best_score = score
                    best_frame = f

            if best_frame:
                _log(f"  FALLBACK: Using frame {best_frame['index']} (score {best_score:.1f}) — {best_frame['action'][:50]}")
                results[marker.index] = f"data:image/png;base64,{best_frame['base64']}"

    _log(f"Selected {len(results)}/{len(markers)} frames")
    return results
