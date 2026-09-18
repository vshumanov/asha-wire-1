package javax.microedition.midlet;
public abstract class MIDlet {
  protected MIDlet(){}
  protected abstract void startApp();
  protected abstract void pauseApp();
  protected abstract void destroyApp(boolean unconditional);
  public final void notifyDestroyed(){}
  public String getAppProperty(String k){return null;}
}
