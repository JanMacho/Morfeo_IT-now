package cf;

import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;

/** Un elemento por cada tag de element_value (B C D F I J S Z s e c @ [). */
@Retention(RetentionPolicy.RUNTIME)
public @interface Everything {
    byte b();
    char c();
    double d();
    float f();
    int i();
    long j();
    short s();
    boolean z();
    String str();
    Color e();
    Class<?> cls();
    Nested nested();
    Color[] colors();
    String[] strs() default {"x"};
}
