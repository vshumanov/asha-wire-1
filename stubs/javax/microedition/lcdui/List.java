package javax.microedition.lcdui;
public class List extends Displayable implements Choice {
  public static final Command SELECT_COMMAND=new Command("",Command.SCREEN,0);
  public List(String title,int type){}
  public int append(String s, Image img){return 0;}
  public int getSelectedIndex(){return 0;}
}
