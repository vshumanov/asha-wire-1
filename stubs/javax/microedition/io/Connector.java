package javax.microedition.io;
import java.io.IOException;
public class Connector {
  public static final int READ=1, WRITE=2, READ_WRITE=3;
  public static Connection open(String url) throws IOException {return null;}
  public static Connection open(String url,int mode) throws IOException {return null;}
}
