"""MPCFill art matching subpackage.

Picks the visually-closest community-rendered art from mpcfill.com using a Scryfall scan
as the reference and writes one PNG per slot (front + DFC back) into the output directory.
Matching uses LightGlue + SuperPoint keypoint correspondences on the cropped art window.
"""
