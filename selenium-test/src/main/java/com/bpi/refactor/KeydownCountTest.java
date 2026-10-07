package com.bpi.refactor;

import org.openqa.selenium.By;
import org.openqa.selenium.JavascriptExecutor;
import org.openqa.selenium.WebDriver;
import org.openqa.selenium.chrome.ChromeDriver;
import org.openqa.selenium.chrome.ChromeOptions;

public class KeydownCountTest {
    public static void main(String[] args) {
        ChromeOptions options = new ChromeOptions();
        options.addArguments("--headless=new");
        WebDriver driver = new ChromeDriver(options);
        try {
            driver.get("data:text/html,<input id='f'>");
            ((JavascriptExecutor) driver).executeScript(
                "window.k=0; document.getElementById('f')" +
                ".addEventListener('keydown', () => window.k++);");

            driver.findElement(By.id("f")).sendKeys("hello world");

            Long count = (Long) ((JavascriptExecutor) driver).executeScript("return window.k;");
            System.out.println(count); // 11, one keydown per character
        } finally {
            driver.quit();
        }
    }
}
