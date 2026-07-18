// auth.ts — login/logout, token management in ui store
// No backend endpoints — pure localStorage

AppRegistry.register('auth', {
  state: {
    tokenSet: !!localStorage.getItem('admin_token'),
    tokenInput: '',
  },

  methods: () => {
    // Logic lives in Alpine.store('ui') now.
    return {};
  },
});

export {};
