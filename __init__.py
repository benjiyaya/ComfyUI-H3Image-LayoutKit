# H3 Layout Kit — bbox-drawn placement prompts for MiniMax-H3-Image (Nodes 1.0)
from .nodes import H3BBoxCanvas, H3LayoutPrompt

NODE_CLASS_MAPPINGS = {
    "H3BBoxCanvas": H3BBoxCanvas,
    "H3LayoutPrompt": H3LayoutPrompt,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "H3BBoxCanvas": "H3 BBox Canvas (Nodes 1.0)",
    "H3LayoutPrompt": "H3 Layout Prompt Builder",
}

WEB_DIRECTORY = "./web"

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
