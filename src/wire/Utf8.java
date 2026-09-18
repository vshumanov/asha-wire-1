package wire;

import java.io.UnsupportedEncodingException;

/** Minimal UTF-8 helper. The digest is small, so it decodes whole (no streaming). */
public final class Utf8 {
    private Utf8() {}

    public static String decode(byte[] b) {
        if (b == null) return "";
        try {
            return new String(b, "UTF-8");
        } catch (UnsupportedEncodingException e) {
            return new String(b);
        }
    }
}
