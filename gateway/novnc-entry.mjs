import RFBModule from "@novnc/novnc/lib/rfb.js";

const RFB = RFBModule?.default ?? RFBModule;

if (typeof RFB !== "function") {
  throw new TypeError("noVNC RFB constructor unavailable");
}

export default RFB;
