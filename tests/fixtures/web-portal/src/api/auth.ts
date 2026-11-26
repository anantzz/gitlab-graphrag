import { httpClient } from "./http";

export interface Session {
  token: string;
  refreshToken: string;
}

/** Log the user in and return a session with access + refresh tokens. */
export async function login(username: string, password: string): Promise<Session> {
  const res = await httpClient.post("/auth/login", { username, password });
  return res.data as Session;
}

export const refreshSession = async (session: Session): Promise<Session> => {
  const res = await httpClient.post("/auth/refresh", { token: session.refreshToken });
  return res.data as Session;
};
