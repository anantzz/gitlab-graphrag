package com.acme.notify;

public interface NotificationChannel {
    void send(String recipient, String subject, String body);
}
