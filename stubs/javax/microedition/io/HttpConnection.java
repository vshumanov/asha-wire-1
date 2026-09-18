package javax.microedition.io;
import java.io.IOException;
import java.io.InputStream;
// Compile-only stub. Spec-correct constants; the phone supplies the real API.
public interface HttpConnection extends Connection {
  int HTTP_OK = 200;
  String GET = "GET";
  int getResponseCode() throws IOException;
  long getLength();
  void setRequestMethod(String method) throws IOException;
  InputStream openInputStream() throws IOException;
}
