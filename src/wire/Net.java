package wire;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import javax.microedition.io.Connector;
import javax.microedition.io.HttpConnection;

/**
 * The one deliberate network touch in the whole suite: a single HTTP GET of the
 * baked digest from the home server, over the LAN, when the user asks (Refresh).
 * Everything else in Wire reads from RMS offline.
 *
 * The URL is BAKED, not a setting -- it is tuned-to-me infrastructure, like the
 * hard-coded specifics in the Schedule app, not a user-facing option. Point it
 * at your home server and rebuild.
 */
public final class Net {

    /** EDIT THIS to your home server. Plain http on the LAN; no cloud, no account. */
    public static final String URL = "http://homeserver.lan:9009/wire.txt";

    private static final int CAP = 64 * 1024;   // a day's digest is tiny; refuse anything absurd
    private String last = "(no pull yet)";

    public String getReport() { return last; }

    /** GET the digest; throws IOException with a short reason on any failure. */
    public byte[] fetch() throws IOException {
        HttpConnection hc = null;
        InputStream in = null;
        try {
            hc = (HttpConnection) Connector.open(URL);
            hc.setRequestMethod(HttpConnection.GET);
            int code = hc.getResponseCode();
            if (code != HttpConnection.HTTP_OK) {
                last = "HTTP " + code;
                throw new IOException("server said HTTP " + code);
            }
            in = hc.openInputStream();
            ByteArrayOutputStream bos = new ByteArrayOutputStream();
            byte[] buf = new byte[1024];
            int rd, total = 0;
            while ((rd = in.read(buf)) >= 0) {
                total += rd;
                if (total > CAP) { last = "too big"; throw new IOException("digest too large"); }
                bos.write(buf, 0, rd);
            }
            last = "ok (" + total + "B)";
            return bos.toByteArray();
        } catch (IOException e) {
            if (last.startsWith("(no")) last = "net err: " + e.getMessage();
            throw e;
        } catch (RuntimeException e) {
            last = "net err: " + e;
            throw new IOException("connect failed: " + e.getMessage());
        } finally {
            if (in != null) try { in.close(); } catch (IOException e) {}
            if (hc != null) try { hc.close(); } catch (IOException e) {}
        }
    }
}
