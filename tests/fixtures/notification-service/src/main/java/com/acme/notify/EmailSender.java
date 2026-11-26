package com.acme.notify;

import org.springframework.mail.javamail.JavaMailSender;

/**
 * Sends transactional email notifications to clients.
 */
public class EmailSender implements NotificationChannel {

    private final JavaMailSender mailSender;

    public EmailSender(JavaMailSender mailSender) {
        this.mailSender = mailSender;
    }

    @Override
    public void send(String recipient, String subject, String body) {
        String html = renderTemplate(subject, body);
        mailSender.send(buildMessage(recipient, html));
    }

    private String renderTemplate(String subject, String body) {
        return "<h1>" + subject + "</h1><p>" + body + "</p>";
    }

    private Object buildMessage(String recipient, String html) {
        return recipient + html;
    }
}
